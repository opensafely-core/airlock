const observer = new MutationObserver((mutations, obs) => {
  const sorterButton = document.querySelector(
    "button.datatable-sorter"
  );
  if (sorterButton) {
    document.querySelector("#airlock-table p.spinner").style.display = "none";
    document.querySelector("#airlock-table table.datatable").style.display = "table";
    obs.disconnect();
    clearTimeout();
    return;
  }
});

observer.observe(document, {
  childList: true,
  subtree: true,
});

// If the datatable hasn't loaded within 5 seconds, it's likely something's gone
// wrong; unhide the table to show the non-datatable table
// Also hide the datatable sort icons as they'll be unformatted without the
// datatable, and they won't work anyway
setTimeout(() => {
  const sorterButton = document.querySelector(
    "button.datatable-sorter"
  );
  if (!sorterButton) {
    document.querySelector("#airlock-table p.spinner").style.display = "none";
    document.querySelector("#airlock-table table.datatable").style.display = "table";
    const sortIcons = document.getElementsByClassName("sort-icon");
    for (let i = 0; i < sortIcons.length; i++) {
      sortIcons.item(i).style.display = "none";;
    }
  }
}, 5000);
