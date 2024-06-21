const observer = new MutationObserver((mutations, obs) => {
  const sorterButton = document.querySelector(
    "button.datatable-sorter"
  );
  if (sorterButton) {
    document.querySelector("#csvtable p.spinner").style.display = "none";
    document.querySelector("#csvtable table.datatable").style.display = "table";
    obs.disconnect();
    return;
  }
});

observer.observe(document, {
  childList: true,
  subtree: true,
});
