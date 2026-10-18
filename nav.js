// QAG Nexus shared nav — highlights the active page
(function () {
  var pages = [
    ["index.html", "Home"],
    ["learn.html", "Learn"],
    ["course.html", "Course"],
    ["simulate.html", "Simulate"],
    ["vessel.html", "Vessel"],
    ["research.html", "Researcher"]
  ];
  var here = (location.pathname.split("/").pop() || "index.html").split("?")[0];
  // lessons live one level down — map them to the Course tab
  var inLessons = location.pathname.indexOf("/lessons/") !== -1;
  if (inLessons) here = "course.html";
  var inLessons = location.pathname.indexOf("/lessons/") !== -1;
  if (inLessons) here = "course.html";
  var prefix = inLessons ? "../" : "";
  var nav = document.createElement("nav");
  nav.className = "nav";
  nav.innerHTML =
    '<div class="nav-inner">' +
    '<a class="brand" href="' + prefix + 'index.html" style="text-decoration:none"><span class="q">QAG</span> NEXUS</a>' +
    '<div class="nav-links">' +
    pages.map(function (p) {
      var active = p[0] === here ? " active" : "";
      return '<a class="' + active.trim() + '" href="' + prefix + p[0] + '">' + p[1] + "</a>";
    }).join("") +
    "</div></div>";
  document.body.prepend(nav);
})();
