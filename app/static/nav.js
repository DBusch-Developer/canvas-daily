// Top-bar hamburger toggle (mobile). Desktop nav shows inline via CSS.
(function () {
  var toggle = document.querySelector(".topbar__toggle");
  var nav = document.getElementById("primary-nav");
  if (!toggle || !nav) return;
  toggle.addEventListener("click", function () {
    var open = nav.classList.toggle("topbar__nav--open");
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
  });
})();
