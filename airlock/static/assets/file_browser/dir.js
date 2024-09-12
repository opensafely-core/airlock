// implement select all checkbox
function toggleSelectAll(elem) {
    const form = document.querySelector("#multiselect_form");

    /** @type {NodeListOf<HTMLInputElement>|undefined} */
    const checkboxes = form?.querySelectorAll('input[type="checkbox"]');

    checkboxes?.forEach(function(checkbox) {
        checkbox.checked = elem.checked;
    });
}
