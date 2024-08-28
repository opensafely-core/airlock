// ensure datatable is initialised when loading over HTMX
window.initCustomTable ? window.initCustomTable() : null;

// implement select all checkbox
function toggleSelectAll(elem, event) {
    const form = document.querySelector("#multiselect_form");

    const checkboxes = form.querySelectorAll('input[type="checkbox"]');

    checkboxes.forEach(function(checkbox) {
        checkbox.checked = elem.checked;
    });
}
