/* dot-claude-iff guided tour - shared behavior for every page under docs/.
   Theme toggle, progress tracking, and prev/next + dot-strip navigation.
   No external resources; must not throw over file:// where storage can throw. */
(function () {
  "use strict";

  var THEME_KEY = "iff-docs-theme";
  var PROGRESS_KEY = "iff-tour-progress";

  var PAGES = [
    ["index.html", "Start"],
    ["understand.html", "1 · Understand"],
    ["setup.html", "2 · Set up"],
    ["learn-1.html", "3.1 · Heartbeat"],
    ["learn-2.html", "3.2 · Pointer"],
    ["learn-3.html", "3.3 · Task"],
    ["learn-4.html", "3.4 · Queue"],
    ["learn-5.html", "3.5 · Map"],
    ["learn-6.html", "3.6 · Ritual"],
    ["done.html", "Finish"]
  ];

  function readProgress() {
    try {
      var raw = localStorage.getItem(PROGRESS_KEY);
      if (!raw) return [];
      var parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch (e) {
      return [];
    }
  }

  function writeProgress(list) {
    try {
      localStorage.setItem(PROGRESS_KEY, JSON.stringify(list));
    } catch (e) {
      /* file:// or storage disabled - progress just will not persist */
    }
  }

  function markVisited(filename) {
    var list = readProgress();
    if (list.indexOf(filename) === -1) {
      list.push(filename);
      writeProgress(list);
    }
    return list;
  }

  function clearProgress() {
    try {
      localStorage.removeItem(PROGRESS_KEY);
    } catch (e) {}
  }

  // The three-surface navigation: every tour page links to the demo and the repo (and the
  // demo banner links back here). Injected once into the shared topbar so all pages agree.
  var REPO_URL = "https://github.com/Sdamirsa/dot-claude-iff";

  function initTopbarLinks() {
    var topbar = document.querySelector(".topbar");
    var themeBtn = document.getElementById("theme");
    if (!topbar || !themeBtn || topbar.querySelector(".topbar-links")) return;
    var box = document.createElement("nav");
    box.className = "topbar-links";
    box.setAttribute("aria-label", "Site");
    [["demo/console.html", "Demo ↗", true], [REPO_URL, "Repo ↗", true]].forEach(function (it) {
      var a = document.createElement("a");
      a.href = it[0]; a.textContent = it[1];
      if (it[2]) { a.target = "_blank"; a.rel = "noopener"; }
      box.appendChild(a);
    });
    topbar.insertBefore(box, themeBtn);
  }

  function initTheme() {
    var btn = document.getElementById("theme");
    if (!btn) return;
    btn.addEventListener("click", function () {
      var root = document.documentElement;
      var current = root.getAttribute("data-theme");
      var next = current === "dark" ? "light"
        : current === "light" ? "dark"
        : (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "light" : "dark");
      root.setAttribute("data-theme", next);
      try { localStorage.setItem(THEME_KEY, next); } catch (e) {}
    });
  }

  function renderTop(currentIndex, visited) {
    var bar = document.createElement("div");
    bar.className = "tour-progressbar";
    bar.setAttribute("aria-hidden", "true");
    var fill = document.createElement("div");
    fill.className = "tour-progressbar-fill";
    var total = PAGES.length;
    var visitedCount = 0;
    for (var i = 0; i < total; i++) {
      if (visited.indexOf(PAGES[i][0]) !== -1) visitedCount++;
    }
    var pct = total > 0 ? Math.round((visitedCount / total) * 100) : 0;
    fill.style.width = pct + "%";
    bar.appendChild(fill);
    document.body.insertBefore(bar, document.body.firstChild);

    var host = document.getElementById("tour-top");
    if (!host) return;

    var nav = document.createElement("nav");
    nav.className = "tour-dots";
    nav.setAttribute("aria-label", "Tour pages");
    for (var j = 0; j < PAGES.length; j++) {
      var file = PAGES[j][0];
      var label = PAGES[j][1];
      var a = document.createElement("a");
      a.href = file;
      a.title = label;
      var isCurrent = currentIndex !== -1 && j === currentIndex;
      var cls = "dot";
      if (isCurrent) cls += " current";
      else if (visited.indexOf(file) !== -1) cls += " visited";
      a.className = cls;
      a.setAttribute("aria-current", isCurrent ? "page" : "false");
      nav.appendChild(a);
    }
    host.appendChild(nav);
  }

  function renderBottomNav(currentIndex) {
    var host = document.getElementById("tour-bottomnav");
    if (!host) return;

    var inner = document.createElement("div");
    inner.className = "tour-bottomnav-inner";

    if (currentIndex > 0) {
      var prev = document.createElement("a");
      prev.className = "btn-nav prev";
      prev.href = PAGES[currentIndex - 1][0];
      prev.textContent = "← " + PAGES[currentIndex - 1][1];
      inner.appendChild(prev);
    } else {
      var spacer = document.createElement("span");
      spacer.className = "btn-nav-spacer";
      inner.appendChild(spacer);
    }

    if (currentIndex !== -1 && currentIndex < PAGES.length - 1) {
      var next = document.createElement("a");
      next.className = "btn-nav next primary";
      next.href = PAGES[currentIndex + 1][0];
      next.textContent = PAGES[currentIndex + 1][1] + " →";
      inner.appendChild(next);
    }

    host.appendChild(inner);
  }

  function initKeyboard(currentIndex) {
    document.addEventListener("keydown", function (e) {
      var target = e.target;
      var tag = (target && target.tagName) || "";
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(tag) || (target && target.isContentEditable)) return;
      if (currentIndex === -1) return;
      if (e.key === "ArrowLeft" && currentIndex > 0) {
        window.location.href = PAGES[currentIndex - 1][0];
      } else if (e.key === "ArrowRight" && currentIndex < PAGES.length - 1) {
        window.location.href = PAGES[currentIndex + 1][0];
      }
    });
  }

  function initReset() {
    var btn = document.getElementById("reset-progress");
    if (!btn) return;
    btn.addEventListener("click", function () {
      clearProgress();
      window.location.reload();
    });
  }

  function init() {
    initTheme();
    initTopbarLinks();
    initReset();

    var body = document.body;
    var pageAttr = (body && body.getAttribute("data-page")) || "";
    var currentIndex = -1;
    for (var i = 0; i < PAGES.length; i++) {
      if (PAGES[i][0] === pageAttr) { currentIndex = i; break; }
    }

    var visited = pageAttr ? markVisited(pageAttr) : readProgress();

    renderTop(currentIndex, visited);
    renderBottomNav(currentIndex);
    initKeyboard(currentIndex);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
