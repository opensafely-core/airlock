import Clusterize from "clusterize.js"

// Clusterize.js (https://clusterize.js.org/) is a library for displaying
// large tables by only creating a relatively small number of <tr> elements
// and updating their contents as you scroll.
// It's good, but missing a key callback so we know when the DOM has been
// updated. E.g. if a sort only changes things not in the DOM, then the
// clusterChanged callback is not called. There are things (like knowing
// when the sort is complete to hide the "sorting" ison) where we need this
// so patching it here:
const insertToDOMOriginal = Clusterize.prototype.insertToDOM;
Clusterize.prototype.insertToDOM = function(rows, cache) {
  // Call original function
  insertToDOMOriginal.call(this, rows, cache);

  // Fire domUpdated callback
  this.options.callbacks.domUpdated && this.options.callbacks.domUpdated();
}

// Get handles for key elements
let containerEl = document.querySelector('.clusterize-table-wrapper');
let scrollEl = document.getElementById("scrollArea");
let contentEl = document.getElementById("contentArea");
let headerEl = document.getElementById("headersArea");
let searchEl = document.getElementById("search-table");
let searchResultsEl = document.querySelector(".search-results");
let searchWrapper = document.querySelector(".search-wrapper");
let headerCells = [...headerEl.querySelector('tr').children];

// CONST strings
const CLASS_SORTING = 'table-sorting';
const CLASS_SORT_ASC = 'sort-ascending';
const CLASS_SORT_DESC = 'sort-descending';
const rows_in_block = 50;
const blocks_in_cluster = 4;
const totalRenderedRows = rows_in_block * blocks_in_cluster;

// Global markers
let tableRows = [];
let isMarkupGenerated = false;
let largestColumnItems = [];
let initialColumnWidths = [];
let isSorting = false;
let isEmpty = false;
let searchResultsMessage = '';
let clusterize;
let sortColumnPositionX;
let sortColumn;
let isSortAscending;

/**
 * If we load another clusterize table without a full page refresh then
 * there are a few things we need to tidy up before we do so.
 */
function cleanUp() {
  
  // Update our DOM references as the html will have all been replaced
  containerEl = document.querySelector('.clusterize-table-wrapper');
  scrollEl = document.getElementById("scrollArea");
  contentEl = document.getElementById("contentArea");
  headerEl = document.getElementById("headersArea");
  searchEl = document.getElementById("search-table");
  searchResultsEl = document.querySelector(".search-results");
  searchWrapper = document.querySelector(".search-wrapper");
  headerCells = [...headerEl.querySelector('tr').children];

  // Reset our initial variables
  tableRows = [];
  isMarkupGenerated = false;
  largestColumnItems = [];
  initialColumnWidths = [];
  isSorting = false;
  isEmpty = false;
  searchResultsMessage = '';
}

/**
 * Ensure the table fills the remaining vertical space on the page. This only works
 * if the entire page content fits within a single screen and the clusterize table
 * has nothing beneath it. That is true for the current two use cases: csv files and
 * the file browser, but might not be true for future use cases so this would need
 * to change.
 */
function fitHeight() {
  scrollEl.style.maxHeight = `calc(99vh - ${scrollEl.getBoundingClientRect().y}px)`;
}

function initializeClusterize() {
  if(!document.getElementById('clusterize-table-content')) {
    // No clusterize table body, so either we've already wired this one up
    // or an error has happened
    return;
  }
  cleanUp();
  fitHeight();

  // Wire up the table using Clusterize.js
  clusterize = new Clusterize({
    rows: processRows(),
    scrollId: 'scrollArea',
    contentId: 'contentArea',
    rows_in_block,
    blocks_in_cluster,
    callbacks: {
      domUpdated: function() {
        updateCellWidths();
      }
    },
    no_data_text: "No results match",
    tag: 'tr' // needed for empty csv files to correctly display "no data" message
  });

  wireUpColumnHeaderSortButtons();
  wireUpSearchBox();

  // We need to update all the cell widths whenever the window resizes. But
  // we use a debounce function so that the update is not called continuously
  window.addEventListener('resize', debounce(updateCellWidths, 150));
}

initializeClusterize();
document.body.addEventListener("htmx:afterSettle", () => initializeClusterize());

/**
 * This keeps all the column widths updated when:
 *  - the table is sorted
 *  - the table is filtered
 *  - the table is scrolled
 */
function updateCellWidths() {
  containerEl.classList.remove('table-loading');
  searchWrapper.classList.remove('searching');

  const firstRowEl = contentEl.querySelector('tr:not(.clusterize-extra-row)');
  if(!firstRowEl) return; // if empty then we bail
  const firstRowCells = [...firstRowEl.children];

  // updateCellWidths is also called when the page resizes, so we
  // may need to adjust the height of the table e.g. if things above
  // the table now take up more/less space
  fitHeight()

  if(initialColumnWidths.length === 0) {
    // Get the font of the first non-row-number table cell element
    const el = firstRowCells[1] || firstRowCells[0]; // in case empty csv file
    const font = getFont(el);

    // Get the cell padding
    const padding = getHorizontalPadding(el);
    initialColumnWidths = largestColumnItems.map(item => padding + getTextWidth(item, font));
  }  

  // Update search message
  searchResultsEl.innerText = searchResultsMessage;

  if (isEmpty || totalRenderedRows > tableRows.length) {
    // If the table is empty then we can collapse it to the width of 
    // it's header cells - potentially then padding them out to fill
    // the screen.

    // Similarly, if the number of rows in the table is less than the
    // amount that clusterize renders, then the entire table will
    // already be rendered and the same logic applies.

    // First reset the header widths
    setCellMinWidths(headerCells, 0);

    // Then if sorting need to update the headers, and stop the sort
    if(isSorting){
      endSort()
    }

    // There is no data so we just want the header to fill available space
    const extraSpace = containerEl.getBoundingClientRect().width - headerEl.getBoundingClientRect().width;
    expandCellsToFixedWidth(headerCells.slice(1), extraSpace);
    return;
  }

  setCellMinWidths(headerCells, initialColumnWidths);
  let containerWidth = containerEl.getBoundingClientRect().width;
  let firstRowWidth = firstRowEl.getBoundingClientRect().width;
  if (isSorting) {
    // When you click on a column header to sort a column you would expect the header
    // to stay in the same place, and not shift left or right. However because the
    // table column widths might change we need to artificially increase the size of
    // some and/or change the scroll position of the table to achieve this effect.

    // First we establish how much space is required by:
    //  - the columns to the left of the sort column
    //  - the sort column itselft
    //  - the columns to the right of the sort column
    let widthToLeft = 0;
    let widthOfSortColumn = 0;
    let widthToRight = 0;
    firstRowCells.forEach((cell, idx) => {
      const cellWidth = cell.getBoundingClientRect().width;
      if (idx < sortColumn) { widthToLeft += cellWidth; }
      else if (idx === sortColumn) { widthOfSortColumn = cellWidth; }
      else { widthToRight += cellWidth; }
    });
    let scrollX = 0;
    if (widthToLeft < sortColumnPositionX) {
      // The space required to the left is not enough. If left as it is then the
      // sort column would shift to the left. So instead we work out how much we
      // need to expand the columns to the left, and make each of them a bit wider
      // to accommodate this. We don't make the row number column wider as this
      // looks a bit odd if it gets really wide.
      const extraSpace = sortColumnPositionX - widthToLeft;
      const cellsToTheLeft = firstRowCells.filter((x,i) => {
        return i < sortColumn && !headerCells[i].classList.contains('clusterize-fixed-width')
      });
      expandCellsToFixedWidth(cellsToTheLeft, extraSpace);
    } else {
      // The space to the left is enough, but maybe too much. We therefore need
      // to scroll the table to the left so that the sort column remains in its
      // original place
      scrollX = widthToLeft - sortColumnPositionX;
    }
    const sortColumnRightHandSideX = sortColumnPositionX + widthOfSortColumn;
    const availableGapToRightOfSortColumn = containerWidth - sortColumnRightHandSideX;
    if (widthToRight < availableGapToRightOfSortColumn) {
      // The columns to the right don't fill the available space. We always want
      // the table to be full width, so we make each cell to the right bigger to
      // fill the gap.
      const extraSpace = availableGapToRightOfSortColumn - widthToRight - 20;
      const cellsToTheRight = firstRowCells.filter((x,i) => {
        return i >= sortColumn && !headerCells[i].classList.contains('clusterize-fixed-width')
      });
      expandCellsToFixedWidth(cellsToTheRight, extraSpace);
    }

    // We scroll the table to ensure the sort column is in place
    scrollEl.scrollLeft = scrollX;

    // Also we have now finished the sort
    endSort()
  } else {
    // If we're not sorting then we just want to ensure the table fills the available
    // space.
    if (firstRowWidth < containerWidth) {
      const extraSpace = containerWidth - firstRowWidth - 20;
      const cellsThatCanExpand = firstRowCells.filter((x,i) => {
        return !headerCells[i].classList.contains('clusterize-fixed-width')
      });
      expandCellsToFixedWidth(cellsThatCanExpand, extraSpace);
    } else {
      setCellMinWidths(firstRowCells, firstRowCells.map(x => 0));

      // if the table is smaller than the window we may need to make it fill the space
      containerWidth = containerEl.getBoundingClientRect().width;
      firstRowWidth = firstRowEl.getBoundingClientRect().width;
      const extraSpace = containerWidth - firstRowWidth - 20;
      const cellsThatCanExpand = firstRowCells.filter((x,i) => {
        return !headerCells[i].classList.contains('clusterize-fixed-width')
      });
      expandCellsToFixedWidth(cellsThatCanExpand, extraSpace);

    }
  }
}

/*
 * Resets the sort ensuring all the loading indicators are hidden
 */
function endSort() {
  headerCells[sortColumn].classList.remove(CLASS_SORTING);
  headerCells[sortColumn].classList.add(isSortAscending ? CLASS_SORT_ASC : CLASS_SORT_DESC);
  isSorting = false;
}

/**
 * Takes an array of table cells and expands them uniformly to fill the
 * extra available space
 * @param {HTMLElement[]} cells 
 * @param {number} width 
 */
function expandCellsToFixedWidth(cells, width) {
  const extraSpacePerCell = Math.max(0, width / cells.length);
  const cellWidths = cells.map((el, i) => {
    return el.getBoundingClientRect().width + extraSpacePerCell;
  });
  setCellMinWidths(cells, cellWidths);
}

/**
 * Sets the min-width style property of an array of table cells. Sets the
 * value to 'unset' if a 0 min-width is passed.
 * @param {HTMLElement[]} cells 
 * @param {number[]} widths 
 */
function setCellMinWidths(cells, widths) {
  cells.forEach((cell, i) => {
    cell.style.minWidth = widths[i] > 0 ? `${widths[i]}px` : 'unset';
  });
}

// 
/**
 * Creates a debounced version of a function that delays its execution until after
 * a specified wait time has elapsed since the last time it was invoked.
 * @param {Function} func - The function to debounce
 * @param {number} wait - The number of milliseconds to delay execution
 * @returns {Function} A debounced version of the input function that:
 *  - Won't execute `func` until the wait time has passed
 *  - Resets the wait time if called again during the delay
 */
function debounce(func, wait) {
  let timeout;
  return (...args) => {
    const later = () => {
      timeout = null;
      func(...args);
    };
    clearTimeout(timeout);
    timeout = setTimeout(later, wait);
  };
}

/**
 * We want to ensure that numeric values are sorted numerically and
 * text values are sorted lexicographically. This sort function
 * handles both situations - and also the case where a column contains
 * a mix of both.
 */
function mixedSort(a, b) {
  // Handle null/undefined
  if (a == null) return -1;
  if (b == null) return 1;
  
  // Convert to numbers if both are numeric strings
  const numA = Number(a);
  const numB = Number(b);
  const isNumA = !isNaN(numA);
  const isNumB = !isNaN(numB);

  // If both are numbers, compare numerically
  if (isNumA && isNumB) {
    return numA - numB;
  }
  
  // If mixed types, numbers come before strings
  if (isNumA) return -1;
  if (isNumB) return 1;
  
  // Both are strings, compare lexicographically
  return String(a).localeCompare(String(b));
}

/**
 * Provides the markup array expected by Clusterize.js, filtered and sorted as appropriate.
 * @param {int} sortIndex The index of the column to be sorted. Null if no sort.
 * @param {boolean} isSortAscending Whether the sort is ascending or descending.
 */
function processRows(sortIndex, isSortAscending) {
  if (!isMarkupGenerated) {
    // First time this is called, so we need to:
    // - find the table rows from the hidden "#clusterize-table-content" tables
    // - extract the text content from each cell for sorting/searching
    // - mark each row as "active" for the search functionality
    // - find the longest (string length) in each field to make a guess as to column width
    //   (this is to reduce the amount the column widths adjust as you sort and filter)

    const tableContent = document.getElementById('clusterize-table-content');
    const tableContentRows = tableContent.querySelectorAll('tbody tr');
    tableContentRows.forEach((row, i) => {
      const cells = Array.from(row.children);
      tableRows.push({
        markup: row.outerHTML,
        active: true,
        data: cells.map((cell) => cell.textContent.trim()),
      })
      tableRows[i].data.forEach((item, idx) => {
        if(largestColumnItems.length < idx + 1) {
          largestColumnItems.push(item);
        } else if(item.length > largestColumnItems[idx].length){
          largestColumnItems[idx] = item;
        }        
      });
    })

    //We can remove the, now redundant, html rows
    tableContent.remove();
    // The row number column can shift around a bit because currently the
    // "longest" value will always end up being 10..0 and the 1 tends to be
    // narrower in most fonts. So let's pretend the highest number is made
    // up of 3s to get a reasonable max width that doesn't shift perceptibly
    largestColumnItems[0] = `${tableRows.length}`.replace(/./g,"3");
    isMarkupGenerated = true;
  }
  if (sortIndex || sortIndex === 0) {
    tableRows.sort((a, b) => {
      const valA = a.data[sortIndex];
      const valB = b.data[sortIndex];
      return isSortAscending ? mixedSort(valA, valB) : mixedSort(valB, valA);
    });
  }
  const markupArray = tableRows.filter(x => x.active).map(x => x.markup);
  isEmpty = markupArray.length === 0;

  if (markupArray.length === tableRows.length) {
    searchResultsMessage = `Showing all ${tableRows.length} rows`;
  } else {
    searchResultsMessage = `Showing ${markupArray.length} row${
      markupArray.length === 1 ? '' : 's'
    } (out of ${tableRows.length})`;
  }

  return markupArray;
}

/**
 * Enables the header sorting.
 */
function wireUpColumnHeaderSortButtons() {
  headerCells.forEach((el, idx) => {
    const button = el.querySelector('button');
    if(!button) return;
    button.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      isSortAscending = !el.classList.contains(CLASS_SORT_ASC);
      headerCells.forEach(header => {
        header.classList.remove(CLASS_SORTING);
        header.classList.remove(CLASS_SORT_ASC);
        header.classList.remove(CLASS_SORT_DESC);
      });
      headerCells[idx].classList.add(CLASS_SORTING);
      isSorting = true;
      sortColumnPositionX = Math.max(
        0, 
        headerCells[idx].getBoundingClientRect().x - containerEl.getBoundingClientRect().x
      );
      sortColumn = idx;

      // So that the "sorting" icon can appear we need to push the table
      // update into the next "tick"
      setTimeout(() => {
        clusterize.update(processRows(idx, isSortAscending));
      },0);
    });
  });
}

/**
 * Enables the search box.
 */
function wireUpSearchBox() {
  // The search strategy is to split the text input into words and filter
  // to rows that contain ALL the words, case insensitive.

  // For fast typers we only want the table to attempt to update
  // during pauses in their typing
  function updateAfterSearch() {
    clusterize.update(processRows());
  }
  const debouncedUpdate = debounce(updateAfterSearch, 120);

  function search() {
    const searchTerms = searchEl.value.toLowerCase().trim().split(/ +/);
    for (let i = 0; i < tableRows.length; i++) {
      tableRows[i].active = true;
      for (let j = 0; j < searchTerms.length; j++) {
        tableRows[i].active = tableRows[i].active && tableRows[i].markup.toLowerCase().indexOf(searchTerms[j]) > -1;
      }
    }

    searchWrapper.classList.add('searching');

    // In large files this can be slow, so we push the table update into
    // the next tick so that the key just pressed appears visible in the
    // search box before the update. Otherwise it feels laggy.
    setTimeout(() => {
      debouncedUpdate();
    }, 0)
  }
  searchEl.addEventListener('input', search);
}

/**
 * Measures the width of text in pixels using a canvas context
 * @param {string} text - The text string to measure
 * @param {string} font - CSS font specification (e.g. "bold 16px Arial")
 * @returns {number} The width of the text in pixels
 */
function getTextWidth(text, font) {
  const canvas = getTextWidth.canvas || (getTextWidth.canvas = document.createElement("canvas"));
  const context = canvas.getContext("2d");
  context.font = font;
  const metrics = context.measureText(text);
  return metrics.width;
}

/**
 * Gets the font string of a DOM element
 * @param {HTMLElement} domElement The element to assess
 * @returns {string} CSS font specification (e.g. "bold 16px Arial")
 */
function getFont(domElement) {
  const fontWeight = getStyleValue(domElement, 'font-weight', 'normal');
  const fontSize = getStyleValue(domElement, 'font-size', '16px');
  const fontFamily = getStyleValue(domElement, 'font-family', 'Times New Roman');
  return `${fontWeight} ${fontSize} ${fontFamily}`;
}

/**
 * Get the horizontal padding (left + right) of a DOM element
 * @param {HTMLElement} domElement The element to assess
 * @returns {number} The horizontal padding in pixels
 */
function getHorizontalPadding(domElement) {
  const paddingLeft = Number.parseFloat(getStyleValue(domElement, 'padding-left', 0));
  const paddingRight = Number.parseFloat(getStyleValue(domElement, 'padding-right', 0));
  return paddingLeft + paddingRight;
}

/**
 * Retrieves the computed style value for a given CSS property of a DOM element
 * @param {HTMLElement} domElement - The DOM element to get the style from
 * @param {string} property - The CSS property name to look up
 * @param {*} valueIfNull - Default value to return if the property is not found
 * @returns {string} The computed style value or the default value if not found
 */
function getStyleValue(domElement, property, valueIfNull) {
  return window.getComputedStyle(domElement).getPropertyValue(property) || valueIfNull;
}