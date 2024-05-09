const observer = new MutationObserver((mutations, obs) => {
  const pageNumberEl = document.querySelector(
    `[data-table-pagination="page-number"]`
  );
  if (pageNumberEl.innerText !== "#") {
    document.querySelector("#csvtable p.spinner").style.display = "none";
    document.querySelector("#csvtable table.datatable").style.display = "table";
    document.querySelector("#pagination-nav").classList.remove("hidden");
    obs.disconnect();
    return;
  }
});

observer.observe(document, {
  childList: true,
  subtree: true,
});
