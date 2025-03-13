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

// Assumes that there is a global variable called 'csvRows' like this:
// [
//  { "data": [row_1_col_1_val, row_1_col_2_val, ...] },
//  { "data": [row_2_col_1_val, row_2_col_2_val, ...] },
//  ...
//  { "data": [row_n_col_1_val, row_n_col_2_val, ...] }
// ]

// Get handles for key elements
const containerEl = document.querySelector('.faster-table-wrapper');
const scrollEl = document.getElementById("scrollArea");
const contentEl = document.getElementById("contentArea");
const headerEl = document.getElementById("headersArea");
const searchEl = document.getElementById("search-table");
const searchWrapper = document.querySelector(".search-wrapper");
const headerCells = [...headerEl.querySelector('tr').children];

// CONST strings
const CLASS_SORTING = 'table-sorting';
const CLASS_SORT_ASC = 'sort-ascending';
const CLASS_SORT_DESC = 'sort-descending';

// Global markers
let isMarkupGenerated = false;
let isSorting = false;
let isEmpty = false;
let clusterize;
let sortColumnPositionX;
let sortColumn;
let isSortAscending;

// Wire up the table using Clusterize.js
clusterize = new Clusterize({
  rows: processRows(),
  scrollId: 'scrollArea',
  contentId: 'contentArea',
  callbacks: {
    domUpdated: function() {
      updateCellWidths();
    }
  }
});

wireUpColumnHeaderSortButtons();
wireUpSearchBox();

// We need to update all the cell widths whenever the window resizes. But
// we use a debounce function so that the update is not called continuously
window.addEventListener('resize', debounce(updateCellWidths, 150));

/**
 * This keeps all the column widths updated when:
 *  - the table is sorted
 *  - the table is filtered
 *  - the table is scrolled
 */
function updateCellWidths() {
  containerEl.classList.remove('table-loading');
  searchWrapper.classList.remove('searching');

  if (isEmpty) {
    // There is no data so we just want the header to fill available space
    const extraSpace = containerEl.getBoundingClientRect().width - headerEl.getBoundingClientRect().width;
    expandCellsToFixedWidth(headerCells.slice(1), extraSpace);
    return;
  }

  // As we're not empty, the cell width is managed by the first row rather than the
  // header, so we reset the min widths
  setCellMinWidths(headerCells, 0);
  const firstRowEl = contentEl.querySelector('tr:not(.clusterize-extra-row)');
  const firstRowCells = [...firstRowEl.children];
  const containerWidth = containerEl.getBoundingClientRect().width;
  const firstRowWidth = firstRowEl.getBoundingClientRect().width;
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
      expandCellsToFixedWidth(firstRowCells.slice(1, sortColumn), extraSpace);
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
      expandCellsToFixedWidth(firstRowCells.slice(sortColumn + 1), extraSpace);
    }

    // We scroll the table to ensure the sort column is in place
    scrollEl.scrollLeft = scrollX;

    // Also we have now finished the sort, so we can update the header class
    headerCells[sortColumn].classList.remove(CLASS_SORTING);
    headerCells[sortColumn].classList.add(isSortAscending ? CLASS_SORT_ASC : CLASS_SORT_DESC);
    isSorting = false;
  } else {
    // If we're not sorting then we just want to ensure the table fills the available
    // space.
    if (firstRowWidth < containerWidth) {
      const extraSpace = containerWidth - firstRowWidth - 20;
      expandCellsToFixedWidth(firstRowCells.slice(1), extraSpace);
    } else {
      setCellMinWidths(firstRowCells, firstRowCells.map(x => 0));
    }
  }
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
 * Provides the markup array expected by Clusterize.js, filtered and sorted as appropriate.
 * @param {int} sortIndex The index of the column to be sorted. Null if no sort.
 * @param {boolean} isSortAscending Whether the sort is ascending or descending.
 */
function processRows(sortIndex, isSortAscending) {
  if (!isMarkupGenerated) {
    // First time this is called, so we need to:
    // - generate the markup that Clusterize.js expects
    // - mark each row as "active" for the search functionality
    // - add the row index to the data array
    for (var i = 0; i < csvRows.length; i++) {
      csvRows[i].markup = `<tr><td class="row-number">${i + 1}</td><td>${csvRows[i].data.join('</td><td>')}</td></tr>`;
      csvRows[i].data.unshift(i + 1);
      csvRows[i].active = true;
    }
    isMarkupGenerated = true;
  }
  if (sortIndex || sortIndex === 0) {
    csvRows.sort((a, b) => {
      if (a.data[sortIndex] > b.data[sortIndex]) return isSortAscending ? 1 : -1;
      if (a.data[sortIndex] < b.data[sortIndex]) return isSortAscending ? -1 : 1;
      return 0;
    });
  }
  const markupArray = csvRows.filter(x => x.active).map(x => x.markup);
  isEmpty = markupArray.length === 0;
  return markupArray;
}

/**
 * Enables the header sorting.
 */
function wireUpColumnHeaderSortButtons() {
  headerCells.forEach((el, idx) => {
    const button = el.querySelector('button');
    button.addEventListener('click', () => {
      isSortAscending = !el.classList.contains(CLASS_SORT_ASC);
      headerCells.forEach(header => {
        header.classList.remove(CLASS_SORTING);
        header.classList.remove(CLASS_SORT_ASC);
        header.classList.remove(CLASS_SORT_DESC);
      });
      headerCells[idx].classList.add(CLASS_SORTING);
      isSorting = true;
      sortColumnPositionX = Math.max(0, headerCells[idx].getBoundingClientRect().x);
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
    for (let i = 0; i < csvRows.length; i++) {
      csvRows[i].active = true;
      for (let j = 0; j < searchTerms.length; j++) {
        csvRows[i].active = csvRows[i].active && csvRows[i].markup.toLowerCase().indexOf(searchTerms[j]) > -1;
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