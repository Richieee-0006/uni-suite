(() => {
  "use strict";

  function initFeedback() {
    const dom = window.UniSuite && window.UniSuite.dom;
    const dialog = document.getElementById("feedback-dialog");
    const form = document.getElementById("feedback-form");
    if (!dialog || !form || !dom) return;

    const subjectSelect = document.getElementById("feedback-subject");
    const titleInput = document.getElementById("feedback-title-input");
    const descInput = document.getElementById("feedback-desc");
    const contactInput = document.getElementById("feedback-contact");
    const websiteInput = document.getElementById("feedback-website");
    const statusOut = document.getElementById("feedback-status");
    const submitBtn = document.getElementById("feedback-submit");

    const state = dom.readState();
    const defaultSubject = state.subject && state.subject.slug ? String(state.subject.slug) : "";

    function clearStatus() {
      if (!statusOut) return;
      dom.clear(statusOut);
      statusOut.hidden = true;
      statusOut.removeAttribute("data-tone");
    }

    function showError(message) {
      if (!statusOut) return;
      dom.clear(statusOut);
      statusOut.dataset.tone = "error";
      statusOut.appendChild(document.createTextNode(String(message || "Chyba při odesílání.")));
      statusOut.hidden = false;
      dom.announce(message, true);
    }

    function showSuccess(issueNumber, issueUrl) {
      if (!statusOut) return;
      dom.clear(statusOut);
      statusOut.dataset.tone = "success";
      const hasValidLink =
        typeof issueUrl === "string" && issueUrl.startsWith("https://github.com/");
      if (hasValidLink && typeof issueNumber === "number") {
        statusOut.appendChild(document.createTextNode("Odesláno na GitHub Issues: "));
        statusOut.appendChild(
          dom.el(
            "a",
            {
              href: issueUrl,
              target: "_blank",
              rel: "noopener noreferrer",
              className: "text-link",
            },
            `#${issueNumber} ↗`
          )
        );
      } else {
        statusOut.appendChild(document.createTextNode("Díky! Zpětná vazba byla odeslána."));
      }
      statusOut.hidden = false;
      dom.announce("Zpětná vazba byla úspěšně odeslána na GitHub Issues.");
    }

    function preselectSubject() {
      if (!subjectSelect || !defaultSubject) return;
      for (const opt of subjectSelect.options) {
        if (opt.value === defaultSubject) {
          subjectSelect.value = defaultSubject;
          break;
        }
      }
    }

    function openFeedback() {
      clearStatus();
      preselectSubject();
      if (!dialog.open) {
        dialog.showModal();
      }
      if (titleInput) {
        titleInput.focus();
      }
    }

    for (const btn of document.querySelectorAll("[data-open-feedback]")) {
      btn.addEventListener("click", openFeedback);
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      clearStatus();

      const kindRadio = form.querySelector('input[name="kind"]:checked');
      const kind = kindRadio ? kindRadio.value : "bug";
      const subject = subjectSelect ? subjectSelect.value : "";
      const title = titleInput ? titleInput.value.trim() : "";
      const description = descInput ? descInput.value.trim() : "";
      const contact = contactInput ? contactInput.value.trim() : "";
      const website = websiteInput ? websiteInput.value.trim() : "";

      if (title.length < 4 || title.length > 120) {
        showError("Název musí mít 4 až 120 znaků.");
        if (titleInput) titleInput.focus();
        return;
      }

      if (description.length < 10 || description.length > 4000) {
        showError("Popis musí mít 10 až 4000 znaků.");
        if (descInput) descInput.focus();
        return;
      }

      const defaultLabel = submitBtn ? submitBtn.textContent : "Odeslat na GitHub";
      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = "Odesílám…";
      }

      try {
        const res = await dom.apiFetch("/api/feedback", {
          method: "POST",
          body: JSON.stringify({
            kind,
            subject,
            title,
            description,
            contact,
            website,
            page_url: window.location.pathname || "/",
          }),
        });
        if (titleInput) titleInput.value = "";
        if (descInput) descInput.value = "";
        showSuccess(res && res.issue_number, res && res.issue_url);
      } catch (err) {
        showError(err && err.message ? err.message : "Odeslání selhalo. Zkus to znovu.");
      } finally {
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.textContent = defaultLabel;
        }
      }
    });
  }

  window.UniSuite = window.UniSuite || {};
  window.UniSuite.initFeedback = initFeedback;
})();
