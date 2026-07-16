// Prevent repeat actions from accidental double-clicks by disabling buttons
// once they've triggered a form submission or HTMX request.

// Keep track of buttons that we've disabled; WeakSet means any that htmx swaps
// out and which are no longer reachable will get GC'd for us
const disabledButtons = new WeakSet();

function disableButton(button) {
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  disabledButtons.add(button);
}

function enableButton(button) {
  if (!disabledButtons.has(button)) return;
  button.disabled = false;
  button.removeAttribute("aria-busy");
  disabledButtons.delete(button);
}

// Note: we don't use any data-hx-* attributes in Airlock; if we did, we might
// want to add them to this selector.
const HTMX_FORM_SELECTOR = "[hx-post], [hx-get], [hx-put], [hx-delete], [hx-patch]";

// Disable submit buttons on standard forms
// We're using a setTimeout(0) so that we do the disabling in the next action
// after the browser has finished processing the submit event.
// This means that (a) the browser has already built the form data (including
// the submit buttons's name/value attributes, and (b) we wait for any synchronous
// preventDefault() from another handler
document.body.addEventListener("submit", (event) => {
  const form = event.target;
  const isHtmx = form.matches(HTMX_FORM_SELECTOR);
  setTimeout(() => {
    // HTMX calls preventDefault to stop normal navigation, so for HTMX forms we
    // still want to disable. For non-HTMX forms, defaultPrevented means another
    // handler cancelled the submission and we should leave the button alone.
    if (event.defaultPrevented && !isHtmx) return;
    // event.submitter could be None if submitted programmatically, in which case, do nothing
    if (event.submitter) disableButton(event.submitter);
  }, 0);
});

// For buttons that trigger HTMX requests directly (no surrounding form), disable
// on request start and re-enable when the request completes so the button is
// usable again after in-place updates.
document.body.addEventListener("htmx:beforeRequest", (event) => {
  const elt = event.detail.elt;
  // Skip buttons that belong to a form — those are already handled by the
  // submit listener above, which fires before htmx:beforeRequest.
  if (elt instanceof HTMLButtonElement && !elt.form) {
    disableButton(elt);
  }
});

document.body.addEventListener("htmx:afterRequest", (event) => {
  const elt = event.detail.elt;
  if (elt instanceof HTMLButtonElement) {
    // Guard against the button having been removed from the DOM by an HTMX
    // swap between the request starting and htmx:afterRequest firing.
    if (elt.isConnected) enableButton(elt);
    return;
  }
  if (elt instanceof HTMLFormElement) {
    elt.querySelectorAll("button[type='submit']").forEach((button) => {
      if (button.isConnected) enableButton(button);
    });
  }
});
