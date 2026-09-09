import { useState } from "react";
import {
  Badge,
  Button,
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
  Input,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../src";
import type { Translate } from "./showcase";
const resources = [
  { name: "Research assistant", kind: "Agent", status: "Ready" },
  { name: "Team handbook", kind: "Knowledge", status: "Ready" },
  { name: "Weekly report", kind: "Workflow", status: "Draft" },
];
export function CollectionDemo({ t }: { t: Translate }) {
  const [query, setQuery] = useState("");
  const visible = resources.filter((item) =>
    item.name.toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <>
      <div>
        <h1 className="text-2xl font-semibold">{t("Resources", "资源")}</h1>
        <p className="mt-2 text-muted-foreground">
          {t(
            "Browse the resources in this workspace.",
            "浏览工作空间中的资源。",
          )}
        </p>
      </div>
      <Input
        type="search"
        aria-label={t("Search resources", "搜索资源")}
        placeholder={t("Search resources…", "搜索资源…")}
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        className="max-w-sm"
      />
      {visible.length ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("Name", "名称")}</TableHead>
              <TableHead>{t("Type", "类型")}</TableHead>
              <TableHead>{t("Status", "状态")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {visible.map((item) => (
              <TableRow key={item.name}>
                <TableCell className="font-medium">{item.name}</TableCell>
                <TableCell>{item.kind}</TableCell>
                <TableCell>
                  <Badge
                    variant={item.status === "Ready" ? "default" : "secondary"}
                  >
                    {item.status}
                  </Badge>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <Empty>
          <EmptyHeader>
            <EmptyTitle>
              {t("No matching resources", "没有匹配的资源")}
            </EmptyTitle>
            <EmptyDescription>
              {t("Try another name.", "试试其他名称。")}
            </EmptyDescription>
          </EmptyHeader>
          <Button variant="outline" onClick={() => setQuery("")}>
            {t("Clear search", "清除搜索")}
          </Button>
        </Empty>
      )}
    </>
  );
}
