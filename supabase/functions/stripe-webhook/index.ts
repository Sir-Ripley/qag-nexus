// QAG Nexus — Stripe webhook edge function
// Deploy: supabase functions deploy stripe-webhook --no-verify-jwt
//   (--no-verify-jwt because Stripe signs with its own secret, not a Supabase JWT;
//    the Stripe signature is verified manually below.)
//
// Secrets required (supabase secrets set):
//   STRIPE_WEBHOOK_SECRET      — from the Stripe webhook endpoint settings
//   STRIPE_RESEARCHER_PRICE_ID — the $30/mo recurring Price id (price_...)
//   SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY — standard
//
// Stripe dashboard → Developers → Webhooks → Add endpoint:
//   URL: https://<project-ref>.supabase.co/functions/v1/stripe-webhook
//   Events: checkout.session.completed, customer.subscription.deleted
//
// IMPORTANT: create the Checkout Session with metadata:
//   { supabase_user_id: "<auth.users id>", price_id: "<STRIPE_RESEARCHER_PRICE_ID>" }
// so the webhook can credit the right account.

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const encoder = new TextEncoder();

// Constant-time string comparison (no timing oracle on the signature check).
function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

// Verifies the Stripe-Signature header per https://docs.stripe.com/webhooks#verify.
async function verifyStripeSignature(
  rawBody: string,
  header: string,
  secret: string,
): Promise<boolean> {
  const parts: Record<string, string> = {};
  for (const kv of header.split(",")) {
    const i = kv.indexOf("=");
    if (i > 0) parts[kv.slice(0, i)] = kv.slice(i + 1);
  }
  const t = parts["t"];
  const v1 = parts["v1"];
  if (!t || !v1 || !secret) return false;
  // Reject replays older than 5 minutes.
  if (Math.abs(Date.now() / 1000 - Number(t)) > 300) return false;

  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, encoder.encode(`${t}.${rawBody}`));
  const hex = [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
  return safeEqual(hex, v1);
}

Deno.serve(async (req: Request): Promise<Response> => {
  const raw = await req.text();
  const signature = req.headers.get("stripe-signature") ?? "";
  const webhookSecret = Deno.env.get("STRIPE_WEBHOOK_SECRET") ?? "";

  if (!(await verifyStripeSignature(raw, signature, webhookSecret))) {
    return new Response("bad signature", { status: 400 });
  }

  const event = JSON.parse(raw);
  const priceId = Deno.env.get("STRIPE_RESEARCHER_PRICE_ID") ?? "";

  // service_role: bypasses RLS — the ONLY writer allowed to touch profiles.tier.
  const admin = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
  );

  if (event.type === "checkout.session.completed") {
    const s = event.data.object;
    const userId: string | undefined = s.metadata?.supabase_user_id;
    const sessionPrice: string | undefined = s.metadata?.price_id;
    if (userId && sessionPrice === priceId) {
      const customer = typeof s.customer === "string" ? s.customer : s.customer?.id ?? null;
      const { error } = await admin
        .from("profiles")
        .update({ tier: "researcher", stripe_customer_id: customer })
        .eq("id", userId);
      if (error) console.error("tier upgrade failed:", error.message);
    } else {
      console.error("checkout completed without matching price metadata; not upgrading");
    }
  } else if (event.type === "customer.subscription.deleted") {
    const sub = event.data.object;
    const customerId = typeof sub.customer === "string" ? sub.customer : sub.customer?.id;
    if (customerId) {
      const { error } = await admin
        .from("profiles")
        .update({ tier: "free" })
        .eq("stripe_customer_id", customerId);
      if (error) console.error("tier downgrade failed:", error.message);
    }
  }
  // All other event types: acknowledge, ignore.

  return new Response("ok");
});
