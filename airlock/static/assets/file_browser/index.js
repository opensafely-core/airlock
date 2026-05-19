// Only disable browser scroll restoration when there is a position to restore
if (sessionStorage.getItem("treeScrollTop")) {
  if ('scrollRestoration' in history) {
    history.scrollRestoration = 'manual';
    document.head.insertAdjacentHTML("beforeend", "<style id='scroll-restore-style'>#tree-container{visibility:hidden}</style>");
  }
}

// keep the selected class up to date in the tree on the client side
function setTreeSelection(tree, event) {
  // target here is the hx-get link that has been clicked on

  // remove class from currently selected node
  tree.querySelector(".selected")?.classList.remove("selected");

  let target = event.srcElement;

  // set current selected
  target.classList.add("selected");
  // ensure parent details container is open, which means clicking on a directory will open containers.
  target.closest("details").open = true;

  // if target link is a filegroup, ensure all child <details> are opened, to match server-side rendering of tree
  if (target.classList.contains("filegroup")) {
    target
      .closest("li.tree")
      .querySelectorAll("details")
      .forEach((e) => (e.open = true));
  }
}

//////////////////////
// Checkbox scripts //
//////////////////////

/**
 * Get an array of all visible checkboxes. Does not return the "select all"
 * checkbox
 * @returns {HTMLInputElement[]} Array of visible checkboxes.
 */
function getVisibleCheckboxes() {
  const form = document.getElementById("multiselect_form");
  const selector = `input[type="checkbox"]:not(.selectall)`;

  return form ? [...form.querySelectorAll(selector)] : [];
}

/**
 * Retrieve from sessionStorage the currently checked checkboxes. Format
 * is { "baseURI": {"checkbox_value": true/false}}
 * @returns {{ [key: string]: { [key:string]: boolean } }}
 */
function getCheckboxSessionState() {
  const stateStr = sessionStorage.getItem("checkbox-cache");

  const state = stateStr ? JSON.parse(stateStr) : {};
  return state;
}

/**
 * Persist the state of the currently checked checkboxes to sessionStorage.
 * This is scoped to the baseURI of the checkbox, which includes the workspace
 * and therefore you avoid the situation where two identical files (apart from
 * the workspace) start conflicting and share their state.
 */
function saveCheckboxSessionState() {
  const currentState = getCheckboxSessionState();
  const checkboxes = getVisibleCheckboxes();

  checkboxes.forEach((checkbox) => {
    if(!currentState[checkbox.baseURI]) currentState[checkbox.baseURI] = {};
    currentState[checkbox.baseURI][checkbox.value] = checkbox.checked;
  });

  sessionStorage.setItem("checkbox-cache", JSON.stringify(currentState));
}

function isReleasedFile(checkbox) {
  return checkbox.closest('tr')?.dataset.released === 'true';
}

// implement select all checkbox
function toggleSelectAll(elem) {
  const checkboxes = getVisibleCheckboxes();

  checkboxes.forEach((checkbox) => {
    // Skip checkboxes for released files that are currently unchecked
    if (isReleasedFile(checkbox) && !checkbox.checked) {
      return;
    }
    checkbox.checked = elem.checked;
  });
  saveCheckboxSessionState();
  updateSelectAllCheckbox();
}

// Update the state of the select all checkbox. Checked if
// all unreleased files are checked; unchecked otherwise
// Then, set visual state to "indeterminate" if any box (released file or not)
// is checked but not all of them
function updateSelectAllCheckbox() {
  const selectAllCheckboxEl = document.querySelector(".selectall");
  if (!selectAllCheckboxEl) return;

  const checkboxes = getVisibleCheckboxes();
  const selected = checkboxes.filter((box) => box.checked);

  const areAllChecked = selected.length === checkboxes.length;
  const areNoneChecked = selected.length === 0;

  if (areNoneChecked) {
    selectAllCheckboxEl.checked = false;
    selectAllCheckboxEl.indeterminate = false;
    return;
  }

  const released = checkboxes.filter(isReleasedFile);

  if (released.length === 0) {
  selectAllCheckboxEl.checked = areAllChecked;
  } else {
    // Checked if all unreleased files are selected (released files don't count)
    selectAllCheckboxEl.checked = selected.filter(cb => !isReleasedFile(cb)).length === checkboxes.length - released.length;
  }
  selectAllCheckboxEl.indeterminate = !areAllChecked;
}


/**
 * Update the UI so the selected checkboxes match the sessionStorage value
 */
function renderCheckboxStatus() {
  const state = getCheckboxSessionState();
  const checkboxes = getVisibleCheckboxes();

  checkboxes.forEach((checkbox) => {
    const savedValue = state[checkbox.baseURI]
      ? state[checkbox.baseURI][checkbox.value]
      : false;
    checkbox.checked = savedValue;
  });
  updateSelectAllCheckbox();
}

/**
 * If the click on the form is for a checkbox (ignoring the selectall checkbox
 * which has it's own logic) then we persist the state to sessionStorage, and
 * check to see if the selectall checkbox needs updating.
 */
function fileBrowserClicked({ target }) {
  if (target.type === "checkbox" && !target.classList.contains("selectall")) {
    saveCheckboxSessionState();
    updateSelectAllCheckbox();
  }
}

/**
 * Add click event listener on the form.
 */
function addCheckboxClickListener() {
  document.getElementById("file-browser-panel").addEventListener("click", fileBrowserClicked);
}

// On first load of the page we need to wire up the event listener
// so that we can respond to checkbox changes.
if (document.readyState !== "loading") {
  // If the document is already loaded then we can add the event listener now
  // and also ensure the checkboxes are in sync with the stored values
  addCheckboxClickListener();
  renderCheckboxStatus();
} else {
  // If the document is not yet loaded we wait for the loaded event
  document.addEventListener("DOMContentLoaded", () => {
    addCheckboxClickListener();
    // I'm pretty sure we never need to call renderCheckboxStatus here
    // because in this scenario the "datatable-ready" event (see below)
    // will fire. But calling it twice isn't an issue, so just in case...
    renderCheckboxStatus();
  });
}

// Every time a datatable is rendered we need to update the checkboxes
// so they match the saved state
document.body.addEventListener("clusterize-table-updated", renderCheckboxStatus);

// Save scroll position before approve/request_changes form submits
document.body.addEventListener("submit", (event) => {
  const form = event.target.closest("form");
  if (!form) return;
  const tree = document.getElementById("tree-container");                    
  if (tree) sessionStorage.setItem("treeScrollTop", tree.scrollTop); 
    sessionStorage.setItem("treeScrollTop", document.getElementById("tree-container").scrollTop);
  }
);

// Restore scroll position on page load
document.addEventListener("DOMContentLoaded", () => {
  const saved = sessionStorage.getItem("treeScrollTop");
  const treeContainer = document.getElementById("tree-container");


  if (saved) {
    sessionStorage.removeItem("treeScrollTop");
    // Wait for browser scroll restoration to complete before applying saved position.
    // The browser may reset scrollTop after DOMContentLoaded, so we delay slightly.
    setTimeout(() => {
      treeContainer.scrollTop = parseInt(saved, 10);
      document.getElementById("scroll-restore-style")?.remove();
    }, 125);
  }
});

