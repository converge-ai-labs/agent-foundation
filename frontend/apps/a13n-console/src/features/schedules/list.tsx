import { ClockIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceIdentity,
  ResourceTable,
  Toolbar,
  useCursor,
} from "../../shared/collection";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { Page } from "../../shared/page";
import { ChoiceFilter } from "../conversations/filters";
import { scheduleQueries } from "./api";
import { ScheduleEditor } from "./editor";
import { ScheduleTiming } from "./timing";

const STATES = ["enabled", "paused", "completed", "archived"] as const;

export function SchedulesPage() {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation(),
    navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [state, setState] = useState<(typeof STATES)[number]>();
  const filters = { state, q: search.trim() || undefined },
    page = useCursor(filters);
  const query = useQuery(
    scheduleQueries(client, workspace.id).page(filters, page.cursor),
  );
  const create =
    can("write") && can("run") ? (
      <ScheduleEditor onSaved={(schedule) => navigate(schedule.id)} />
    ) : undefined;
  return (
    <Page
      title={t("Schedules")}
      description={t(
        "Run agents once or on a recurring schedule, with a separate result for each trigger.",
      )}
      actions={create}
      toolbar={
        <Toolbar
          search={search}
          onSearchChange={setSearch}
          searchLabel={t("Search schedules")}
          filters={
            <ChoiceFilter
              label={t("State")}
              allLabel={t("All states")}
              value={state}
              onChange={(value) =>
                setState(STATES.find((option) => option === value))
              }
              options={STATES.map((value) => ({
                value,
                label: t(
                  value === "completed"
                    ? "Scheduling finished"
                    : `state.${value}`,
                ),
              }))}
            />
          }
        />
      }
    >
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading variant="table" columns={4} />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            caption={t("Schedules")}
            items={query.data.items}
            onRowActivate={(schedule) => navigate(schedule.id)}
            columns={[
              {
                label: t("Schedule"),
                tone: "primary",
                render: (schedule) => (
                  <ResourceIdentity
                    to={schedule.id}
                    name={schedule.name}
                    description={schedule.description}
                    resourceId={schedule.id}
                    icon={<ClockIcon size={16} />}
                  />
                ),
              },
              {
                label: t("State"),
                render: (schedule) => (
                  <StatePill
                    state={schedule.state}
                    label={
                      schedule.state === "completed"
                        ? t("Scheduling finished")
                        : undefined
                    }
                  />
                ),
              },
              {
                label: t("Timing"),
                render: (schedule) => (
                  <ScheduleTiming rule={schedule.schedule} />
                ),
              },
              {
                label: t("Updated"),
                tone: "muted",
                render: (schedule) => (
                  <Timestamp value={schedule.updated_at} relative />
                ),
              },
            ]}
          />
          <CollectionFooter
            count={t("{{count}} schedules on this page", {
              count: query.data.items.length,
            })}
          >
            <Pagination page={page} next={query.data.next_cursor} />
          </CollectionFooter>
        </>
      ) : (
        !query.error && (
          <Empty
            icon={<ClockIcon size={20} />}
            title={t(
              search || state ? "No matching schedules" : "No schedules yet",
            )}
            description={t(
              search || state
                ? "Change or clear the search and filters."
                : "Choose an agent, input and timing to create a schedule.",
            )}
            action={!search && !state && create}
          />
        )
      )}
    </Page>
  );
}
