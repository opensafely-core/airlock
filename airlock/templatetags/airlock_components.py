from django import template
from slippers.templatetags.slippers import register_components  # type: ignore


register = template.Library()


register_components(
    {
        # Airlock
        "airlock_header": "_components/header/base.html",
        "airlock_workspace_header": "_components/header/workspace/header.html",
        "airlock_request_header": "_components/header/request/header.html",
        "airlock_repo_header": "_components/header/repo/header.html",
        "datatable": "_components/datatable.html",
        # Components
        "article_header": "_components/article/header.html",
        "alert": "_components/alert/alert.html",
        "breadcrumb": "_components/breadcrumbs/breadcrumb.html",
        "breadcrumbs": "_components/breadcrumbs/breadcrumbs-container.html",
        "button": "_components/button.html",
        "card": "_components/card/card.html",
        "card_footer": "_components/card/card-footer.html",
        "code": "_components/code.html",
        "description_list": "_components/description-list.html",
        "description_item": "_components/description-item.html",
        "footer": "_components/footer.html",
        "form_checkbox": "_components/form/checkbox.html",
        "form_fieldset": "_components/form/fieldset.html",
        "form_input": "_components/form/input.html",
        "form_legend": "_components/form/legend.html",
        "form_radio": "_components/form/radio.html",
        "form_radios": "_components/form/radio_list.html",
        "form_select": "_components/form/select.html",
        "form_textarea": "_components/form/textarea.html",
        "grid_three_cols": "_components/grid/three-cols.html",
        "grid_col_span_1": "_components/grid/col-span-1.html",
        "grid_col_lg_span_2": "_components/grid/col-lg-span-2.html",
        "header": "_components/header.html",
        "link": "_components/link.html",
        "list_group": "_components/list-group/list-group.html",
        "list_group_empty": "_components/list-group/list-group-empty.html",
        "list_group_item": "_components/list-group/list-group-item.html",
        "list_group_rich_item": "_components/list-group/list-group-rich-item.html",
        "log_item": "_components/log-item.html",
        "modal": "_components/modal/modal.html",
        "multiselect": "_components/multiselect/multiselect.html",
        "multiselect_option": "_components/multiselect/multiselect-option.html",
        "pill": "_components/pill/pill.html",
        "pill_application_status": "_components/pill/application-status.html",
        "pill_project_status": "_components/pill/project-status.html",
        "skip_link": "_components/skip-link.html",
        "table_body": "_components/table/table-body.html",
        "table_cell": "_components/table/table-cell.html",
        "table_head": "_components/table/table-head.html",
        "table_header": "_components/table/table-header.html",
        "table_pagination": "_components/table/table-pagination.html",
        "table_row": "_components/table/table-row.html",
        "table": "_components/table/table.html",
        "time": "_components/time.html",
        "timeline_item": "_components/timeline-item.html",
        "tooltip": "_components/tooltip.html",
        # Partials
        "alerts": "_partials/alerts.html",
        "card_pagination": "_partials/card-pagination.html",
        "latest_job_requests_table": "_partials/latest-job-requests-table.html",
        "staff_hero": "_partials/staff-hero.html",
        # Icons
        "icon_academic_cap_outline": "_icons/academic-cap-outline.svg",
        "icon_arrow_down_mini": "_icons/arrow-down-mini.svg",
        "icon_arrow_up_mini": "_icons/arrow-up-mini.svg",
        "icon_arrows_up_down_mini": "_icons/arrows-up-down-mini.svg",
        "icon_beaker_outline": "_icons/beaker-outline.svg",
        "icon_branches_outline": "_icons/branches-outline.svg",
        "icon_building_library_outline": "_icons/building-library-outline.svg",
        "icon_calendar_outline": "_icons/calendar-outline.svg",
        "icon_check_circle_solid": "_icons/check-circle-solid.svg",
        "icon_check_outline": "_icons/check-outline.svg",
        "icon_chevron_down_mini": "_icons/chevron-down-mini.svg",
        "icon_chevron_right_outline": "_icons/chevron-right-outline.svg",
        "icon_chevron_up_mini": "_icons/chevron-up-mini.svg",
        "icon_clipboard_document_check_outline": "_icons/clipboard-document-check-outline.svg",
        "icon_clock_outline": "_icons/clock-outline.svg",
        "icon_clock_solid": "_icons/clock-solid.svg",
        "icon_code_bracket_outline": "_icons/code-bracket-outline.svg",
        "icon_custom_spinner": "_icons/custom/spinner.svg",
        "icon_ellipsis_horizontal_outline": "_icons/ellipsis-horizontal-outline.svg",
        "icon_exclamation_circle_mini": "_icons/exclamation-circle-mini.svg",
        "icon_exclamation_triangle_outline": "_icons/exclamation-triangle-outline.svg",
        "icon_exclamation_triangle_solid": "_icons/exclamation-triangle-solid.svg",
        "icon_folder_open_outline": "_icons/folder-open-outline.svg",
        "icon_folder_outline": "_icons/folder-outline.svg",
        "icon_github_outline": "_icons/github-outline.svg",
        "icon_home_outline": "_icons/home-outline.svg",
        "icon_information_circle_solid": "_icons/information-circle-solid.svg",
        "icon_lifebuoy_outline": "_icons/lifebuoy-outline.svg",
        "icon_lock_closed_solid": "_icons/lock-closed-solid.svg",
        "icon_menu_outline": "_icons/menu-outline.svg",
        "icon_pencil_outline": "_icons/pencil-outline.svg",
        "icon_play_outline": "_icons/play-outline.svg",
        "icon_queue_list_outline": "_icons/queue-list-outline.svg",
        "icon_rectangle_stack_outline": "_icons/rectangle-stack-outline.svg",
        "icon_status_online_outline": "_icons/status-online-outline.svg",
        "icon_user_group_outline": "_icons/user-group-outline.svg",
        "icon_user_outline": "_icons/user-outline.svg",
        "icon_x_circle_solid": "_icons/x-circle-solid.svg",
        "icon_x_outline": "_icons/x-outline.svg",
    },
    register,
)
