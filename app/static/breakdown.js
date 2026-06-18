// Detail-page modal for the AI breakdown. Progressive enhancement: without this
// script the form still POSTs and returns the full breakdown page.
(function () {
  var dialog = document.getElementById("breakdown-dialog");
  var trigger = document.getElementById("breakdown-trigger");
  var body = document.getElementById("breakdown-body");
  if (!dialog || !trigger || !body || typeof dialog.showModal !== "function") return;

  trigger.addEventListener("click", function () {
    body.innerHTML =
      '<p class="modal__loading"><span class="spinner"></span> Thinking it through…</p>';
    dialog.showModal();
  });

  dialog.querySelectorAll("[data-close]").forEach(function (btn) {
    btn.addEventListener("click", function () { dialog.close(); });
  });
  dialog.addEventListener("click", function (e) {
    if (e.target === dialog) dialog.close(); // click on backdrop
  });

  var copyBtn = dialog.querySelector("[data-copy]");
  if (copyBtn) {
    copyBtn.addEventListener("click", function () {
      var root = body.querySelector("[data-breakdown]");
      if (!root) return;
      var text = "";
      root.querySelectorAll(".section-card").forEach(function (card) {
        text += card.querySelector(".section-card__title").textContent + "\n";
        card.querySelectorAll(".section-card__body p").forEach(function (p) {
          text += "- " + p.textContent + "\n";
        });
        text += "\n";
      });
      navigator.clipboard.writeText(text.trim()).then(function () {
        copyBtn.textContent = "Copied!";
        setTimeout(function () { copyBtn.textContent = "Copy"; }, 1500);
      });
    });
  }
})();
