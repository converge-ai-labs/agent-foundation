import { i18n } from "../i18n";
import type { Schema } from "../transport/client";

export const readOnly: Schema<"EnvironmentAction">[] = [
  "environment.file.stat",
  "environment.file.read_text",
  "environment.file.read_bytes",
  "environment.file.list",
  "environment.file.query",
  "environment.file.search_text",
  "environment.file.copy_source",
];

// Store an exact ceiling: omitted ceilings retain legacy file/execution access.
export const fullControl: Schema<"EnvironmentAction">[] = [
  ...readOnly,
  "environment.file.write_text",
  "environment.file.patch_text",
  "environment.file.mkdir",
  "environment.file.move",
  "environment.file.remove",
  "environment.file.write_bytes",
  "environment.file.copy_destination",
  "environment.shell.exec",
  "environment.process.list",
  "environment.process.start",
  "environment.process.inspect",
  "environment.process.read_output",
  "environment.process.write_stdin",
  "environment.process.close_stdin",
  "environment.process.signal",
  "environment.process.wait",
  "environment.process.kill",
  "environment.process.release",
  "environment.output.read",
  "environment.output.release",
  "environment.port.inspect",
  "environment.port.wait",
  "environment.state.export",
  "environment.state.restore",
  "environment.computer.describe",
  "environment.computer.observe",
  "environment.computer.zoom",
  "environment.computer.click",
  "environment.computer.move",
  "environment.computer.drag",
  "environment.computer.scroll",
  "environment.computer.type_text",
  "environment.computer.press_keys",
  "environment.computer.read_clipboard",
  "environment.computer.write_clipboard",
  "environment.computer.a11y_describe",
  "environment.computer.a11y_roots",
  "environment.computer.a11y_inspect",
  "environment.computer.a11y_find",
  "environment.computer.a11y_read",
  "environment.computer.a11y_range",
  "environment.computer.a11y_range_read",
  "environment.computer.ax_disclosing_set",
  "environment.computer.ax_expanded_set",
  "environment.computer.ax_focus",
  "environment.computer.ax_focused_window_set",
  "environment.computer.ax_main_set",
  "environment.computer.ax_minimized_set",
  "environment.computer.ax_perform_action",
  "environment.computer.ax_position_set",
  "environment.computer.ax_selected_children_set",
  "environment.computer.ax_selected_columns_set",
  "environment.computer.ax_selected_rows_set",
  "environment.computer.ax_selected_set",
  "environment.computer.ax_selected_text_set",
  "environment.computer.ax_size_set",
  "environment.computer.ax_text_range_select",
  "environment.computer.ax_text_ranges_select",
  "environment.computer.ax_value_number_set",
  "environment.computer.ax_value_string_set",
  "environment.computer.ax_visible_text_range_set",
  "environment.computer.uia_collapse",
  "environment.computer.uia_dock_set",
  "environment.computer.uia_expand",
  "environment.computer.uia_focus",
  "environment.computer.uia_invoke",
  "environment.computer.uia_legacy_default_action",
  "environment.computer.uia_legacy_select",
  "environment.computer.uia_legacy_value_set",
  "environment.computer.uia_range_add_selection",
  "environment.computer.uia_range_remove_selection",
  "environment.computer.uia_range_scroll_into_view",
  "environment.computer.uia_range_select",
  "environment.computer.uia_range_show_context_menu",
  "environment.computer.uia_range_value_set",
  "environment.computer.uia_realize",
  "environment.computer.uia_scroll_amount",
  "environment.computer.uia_scroll_into_view",
  "environment.computer.uia_scroll_percent",
  "environment.computer.uia_selection_add",
  "environment.computer.uia_selection_remove",
  "environment.computer.uia_selection_select",
  "environment.computer.uia_show_context_menu",
  "environment.computer.uia_toggle",
  "environment.computer.uia_transform_move",
  "environment.computer.uia_transform_resize",
  "environment.computer.uia_transform_rotate",
  "environment.computer.uia_transform_zoom",
  "environment.computer.uia_transform_zoom_unit",
  "environment.computer.uia_value_set",
  "environment.computer.uia_view_set",
  "environment.computer.uia_window_close",
  "environment.computer.uia_window_state_set",
  "environment.computer.atspi_caret_set",
  "environment.computer.atspi_component_extents_set",
  "environment.computer.atspi_component_position_set",
  "environment.computer.atspi_component_size_set",
  "environment.computer.atspi_do_action",
  "environment.computer.atspi_document_selections_set",
  "environment.computer.atspi_focus",
  "environment.computer.atspi_range_scroll_to",
  "environment.computer.atspi_range_scroll_to_point",
  "environment.computer.atspi_scroll_to",
  "environment.computer.atspi_scroll_to_point",
  "environment.computer.atspi_selection_add_child",
  "environment.computer.atspi_selection_all",
  "environment.computer.atspi_selection_clear",
  "environment.computer.atspi_selection_remove_child",
  "environment.computer.atspi_selection_remove_selected",
  "environment.computer.atspi_table_column_select",
  "environment.computer.atspi_table_column_unselect",
  "environment.computer.atspi_table_row_select",
  "environment.computer.atspi_table_row_unselect",
  "environment.computer.atspi_text_contents_set",
  "environment.computer.atspi_text_copy",
  "environment.computer.atspi_text_cut",
  "environment.computer.atspi_text_delete",
  "environment.computer.atspi_text_insert",
  "environment.computer.atspi_text_paste",
  "environment.computer.atspi_text_selection_add",
  "environment.computer.atspi_text_selection_remove",
  "environment.computer.atspi_text_selection_set",
  "environment.computer.atspi_value_set",
];

export function permissionPreset(
  ceiling: Schema<"EnvironmentPermissionSet"> | undefined,
): "read_only" | "full" | undefined {
  if (!ceiling) return undefined;
  const operations = new Set(ceiling.operations ?? []);
  for (const [preset, actions] of [
    ["read_only", readOnly],
    ["full", fullControl],
  ] as const) {
    if (
      operations.size === actions.length &&
      actions.every((action) => operations.has(action))
    )
      return preset;
  }
  return undefined;
}

export function permissionLabel(
  ceiling: Schema<"EnvironmentPermissionSet"> | undefined,
) {
  switch (permissionPreset(ceiling)) {
    case "read_only":
      return i18n.t("Read only");
    case "full":
      return i18n.t("Full control");
    default:
      return ceiling
        ? i18n.t("{{count}} allowed action", {
            count: ceiling.operations?.length ?? 0,
          })
        : i18n.t("Files and execution");
  }
}
