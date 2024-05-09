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
