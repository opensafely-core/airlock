import { DataTable } from "simple-datatables";
import "../styles/datatable.css";

/**
 * @param {string} value
 */
function hasOnlyDigits(value) {
  return /^\d+$/.test(value);
}

function buildTables() {
  /** @type {NodeListOf<HTMLTableElement> | null} */
  // In some situations the buildTables is called twice. There may be better
  // ways of doing this, but by restricting the selector to just html tables
  // that have not already been DataTable-ified we solve the issue. This works
  // because tables that need to be processed contain the attribute "data-datatable"
  // and those that already have, contain the .datatable-table css class.
  const datatableEls = document.querySelectorAll("[data-datatable]:not(.datatable-table)");

  datatableEls?.forEach((table) => {
    const columnFilter = table.hasAttribute("data-column-filter");
    const searchable = table.hasAttribute("data-searchable");
    const sortable = table.hasAttribute("data-sortable");

    let paging = false;
    let perPage = undefined;
    if (table.hasAttribute("data-per-page")) {
      const dataPerPage = table.getAttribute("data-per-page");
      if (dataPerPage !== null && hasOnlyDigits(dataPerPage)) {
        paging = true;
        perPage = parseInt(dataPerPage);
      }
    }

    let dataTable = new DataTable(table, {
      classes: {
        sorter: "datatable-sorter p-2 relative text-left w-full",
        table: `
        datatable-table min-w-full divide-y divide-slate-300
        [&_th]:bg-slate-200 [&_th]:text-slate-900 [&_th]:text-sm [&_th]:font-semibold [&_th]:leading-5 [&_th]:text-left [&_th]:whitespace-nowrap [&_th]:w-auto
        [&_td]:text-slate-700 [&_td]:text-sm [&_td]:p-2
        [&_tr]:border-t [&_tr]:border-slate-200 first:[&_tr]:border-t-0 [&_tr]:bg-white even:[&_tr]:bg-slate-50
      `,
        top: "hidden",
        bottom: paging
          ? "datatable-bottom flex flex-col items-center gap-1 py-3 px-4 border-t border-slate-200 w-full text-sm"
          : "hidden",
        pagination: paging ? "datatable-pagination" : "hidden",
        paginationList: paging
          ? "datatable-pagination-list flex flex-row gap-4 mx-auto"
          : "hidden",
        paginationListItemLink: paging
          ? `
        font-semibold text-oxford-600 underline underline-offset-2 decoration-oxford-300 transition-colors duration-200
        hover:decoration-transparent hover:text-oxford
        focus:decoration-transparent focus:text-oxford focus:bg-bn-sun-300
      `
          : "hidden",
      },
      paging,
      perPage,
      searchable,
      sortable,
      tableRender: columnFilter
        ? (_data, table) => {
            const tHead = table.childNodes?.[0];
            const filterHeaders = {
              nodeName: "TR",
              childNodes: tHead?.childNodes?.[0].childNodes?.map(
                (_th, index) => {
                  const showSearch =
                    // @ts-ignore
                    _th.attributes["data-searchable"] !== "false";

                  return {
                    nodeName: "TH",
                    childNodes: showSearch
                      ? [
                          {
                            nodeName: "INPUT",
                            attributes: {
                              class:
                                "datatable-input block w-full border-slate-300 font-normal shadow-sm rounded-md mb-1 sm:text-sm sm:leading-5 focus:border-oxford-500 focus:outline-oxford-500 focus:-outline-offset-1",
                              "data-columns": `[${index}]`,
                              // @ts-ignore
                              placeholder: `Filter ${_data.headings[index].text
                                .trim()
                                .toLowerCase()}`,
                              type: "search",
                            },
                          },
                        ]
                      : [],
                  };
                }
              ),
            };
            tHead?.childNodes?.push(filterHeaders);
            return table;
          }
        : (_data, table) => table,
    });

    dataTable.on("datatable.init", () => {
      const container = table.closest(".table-container");

      if (container) {
        const spinner = container.querySelector("[data-datatable-spinner]");
        const wrapper = container.querySelector("[data-datatable-wrapper]");
        spinner?.classList.toggle("hidden");
        wrapper.classList.toggle("hidden");
      }

      // We want to display some visual indication when the table is sorting
      // because if it takes a long time the user can think nothing is
      // happening. However, the way sorting is implemented in simple
      // datatables means there is no guaranteed way to update the UI before
      // the sort completes. There is a datatables.sorting event which fires
      // before the sort, and a datatables.sort event which fires after. But
      // they're in a single thread meaning the UI won't update between them.
      // As a workaround, we
      // - intercept the click on the table header that triggers the sort
      // - prevent the click propagating to the event listener in the datatable library
      // - make the css change (just adding a class) to update the UI with a spinner
      // - trigger the click programmatically
      const sorters = table.querySelectorAll('.datatable-sorter');
      sorters.forEach((sorter) => {
        sorter.addEventListener('click', (e) => {
          if(!e.isTrusted) {
            // If not isTrusted, then this click was triggered by our script
            // below, so we've already done our UI handling and can let the
            // simple-datatable library do its thing
            return;
          }
          // Prevent the datatables sorting from happening until the UI has
          //  been visually updated
          e.preventDefault();
          e.stopPropagation();
          
          requestAnimationFrame(() => {
            // Show loading state in the next animation frame
            sorter.closest('th').classList.add('datatable-sorting') 

            // Finally, retrigger the click of the header in the next animation frame
            requestAnimationFrame(() => {
              sorter.click();
            });
          })

        }, true);
      });

      // For remembering the state of the checkboxes we need to update them
      // from sessionStorage every time the table is redrawn. Rather than
      // adding the checkbox logic here, we decouple it by just emitting an
      // event when the table is ready, and the checkbox state logic can listen for it.
      document.body.dispatchEvent(new Event("datatable-ready"));
    });
  });
}

buildTables();

document.body.addEventListener("htmx:afterSettle", () => buildTables());
