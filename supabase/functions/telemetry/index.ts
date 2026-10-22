// QAG Nexus — telemetry edge function
// Deploy: supabase functions deploy telemetry
//   (keep Supabase JWT verification ON — callers send the user's access token)
//
// GET  /telemetry       → { tier, fits[≤50 newest], progress[] } for the caller
// POST /telemetry/fits  → queue a fit run (researcher tier only; RLS 403s free users)
//
// Auth: the user's Supabase access token in the Authorization header.
// RLS does the authorization — this function never bypasses it.

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, content-type",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS, "Content-Type": "application/json" },
  });
}

Deno.serve(async (req: Request): Promise<Response> => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });

  const authHeader = req.headers.get("Authorization") ?? "";
  const supabase = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_ANON_KEY")!,
    { global: { headers: { Authorization: authHeader } } },
  );

  // Validates the caller's JWT; RLS policies below see auth.uid() = this user.
  const {
    data: { user },
    error: authError,
  } = await supabase.auth.getUser();
  if (authError || !user) return json({ error: "unauthorized" }, 401);

  const url = new URL(req.url);

  // ── GET: dashboard payload ─────────────────────────────────────────────
  if (req.method === "GET" && !url.pathname.endsWith("/fits")) {
    const [{ data: profile }, { data: fits }, { data: progress }] = await Promise.all([
      supabase.from("profiles").select("tier, display_name").eq("id", user.id).single(),
      supabase
        .from("fits")
        .select("id, dataset_version, status, result, created_at")
        .order("created_at", { ascending: false })
        .limit(50),
      supabase.from("course_progress").select("lesson, completed_at"),
    ]);
    return json({
      tier: profile?.tier ?? "free",
      display_name: profile?.display_name ?? null,
      fits: fits ?? [],
      progress: progress ?? [],
    });
  }

  // ── POST /fits: queue an optimization run (researcher-gated by RLS) ────
  if (req.method === "POST" && url.pathname.endsWith("/fits")) {
    const body = await req.json().catch(() => ({}));
    const { data, error } = await supabase
      .from("fits")
      .insert({
        user_id: user.id,
        dataset_version: body.dataset_version ?? "sparc-2024.1",
        params: body.params ?? {},
      })
      .select("id, status, created_at")
      .single();

    // RLS denies the insert for non-researchers → honest 403, no fake queue.
    if (error) return json({ error: "Researcher tier required to queue fits." }, 403);
    return json({ fit: data }, 201);
  }

  return json({ error: "not found" }, 404);
});
