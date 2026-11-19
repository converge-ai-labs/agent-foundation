import {
  React,
  Badge,
  Button,
  Checkbox,
  ChoiceField,
  Field,
  FieldControl,
  FieldDescription,
  FieldError,
  FieldItem,
  FieldLabel,
  FieldValidity,
  FormField,
  Input,
  Label,
  ReadOnlyField,
  SearchPicker,
  SegmentedControl,
  Select,
  SelectButton,
  SelectContent,
  SelectGroup,
  SelectGroupLabel,
  SelectItem,
  SelectLabel,
  SelectPopup,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
  Switch,
  Textarea,
  useState,
  useToast,
  type AppContribution,
} from "../src/app-plugins/bridge";

const options = [
  { label: "Detailed", value: "detailed" },
  { label: "Compact", value: "compact" },
];
const regions = [
  "Tokyo",
  "Shanghai",
  "Singapore",
  "Sydney",
  "London",
  "Paris",
  "Frankfurt",
  "Toronto",
  "Seattle",
].map((label) => ({ label, value: label }));

export function Controls() {
  const [choice, setChoice] = useState("detailed");
  const [region, setRegion] = useState<string>();
  const [mode, setMode] = useState("detailed");
  const [showSummary, setShowSummary] = useState(false);
  const toast = useToast();

  return (
    <div className="mx-auto grid max-w-2xl gap-6 p-6">
      <h1 className="text-xl font-semibold">App Plugin controls</h1>

      <section aria-label="Select components" className="grid gap-4">
        <Select items={options} defaultValue="detailed">
          <SelectLabel>Report format</SelectLabel>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectPopup>
            <SelectGroup>
              <SelectGroupLabel>Standard</SelectGroupLabel>
              <SelectItem value="detailed">Detailed</SelectItem>
            </SelectGroup>
            <SelectSeparator />
            <SelectGroup>
              <SelectGroupLabel>Compact view</SelectGroupLabel>
              <SelectItem value="compact">Compact</SelectItem>
            </SelectGroup>
          </SelectPopup>
        </Select>

        <Select items={options} defaultValue="detailed">
          <SelectLabel>Report export format</SelectLabel>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {options.map(({ label, value }) => (
              <SelectItem key={value} value={value}>
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <SelectButton
          aria-expanded={showSummary}
          onClick={() => setShowSummary(!showSummary)}
        >
          Selection summary
        </SelectButton>
        {showSummary && (
          <ReadOnlyField label="Selected options">
            {options.find((option) => option.value === choice)?.label}
            {region && ` · ${region}`}
          </ReadOnlyField>
        )}
      </section>

      <section aria-label="Choice patterns" className="grid gap-4">
        <ChoiceField
          label="Default layout"
          options={options}
          value={choice}
          onValueChange={setChoice}
        />
        <SearchPicker
          label="Region"
          placeholder="Search regions"
          emptyMessage="No matching regions"
          groups={[{ label: "Regions", options: regions }]}
          value={region}
          onValueChange={setRegion}
        />
        <SegmentedControl
          label="Preview mode"
          options={options}
          value={mode}
          onValueChange={setMode}
        />
      </section>

      <section aria-label="Form components" className="grid gap-4">
        <Field
          validate={(value) =>
            typeof value === "string" && value.length > 0 && value.length < 3
              ? "Report code is too short."
              : null
          }
          validationMode="onChange"
        >
          <FieldLabel>Report code</FieldLabel>
          <FieldControl className="rounded-md border border-input bg-background px-3 py-2" />
          <FieldDescription>Enter at least three characters.</FieldDescription>
          <FieldError />
          <FieldValidity>
            {({ validity }) => (
              <span>
                {validity.valid === false
                  ? "Code invalid"
                  : validity.valid
                    ? "Code valid"
                    : "Code unchecked"}
              </span>
            )}
          </FieldValidity>
        </Field>
        <FormField label="Report name">
          <Input placeholder="Quarterly report" />
        </FormField>
        <FormField label="Notes">
          <Textarea rows={2} />
        </FormField>
        <Field>
          <FieldItem className="items-center gap-2">
            <Checkbox id="include-details" />
            <FieldLabel htmlFor="include-details">Include details</FieldLabel>
          </FieldItem>
        </Field>
        <div className="flex items-center gap-2">
          <Switch id="email-summary" />
          <Label htmlFor="email-summary">Email summary</Label>
        </div>
        <ReadOnlyField label="Plugin status">
          <Badge>Enabled</Badge>
        </ReadOnlyField>
        <Button onClick={() => toast.add({ title: "Preview ready" })}>
          Preview report
        </Button>
      </section>
    </div>
  );
}

export const controlsPage = {
  id: "controls",
  label: "UI controls",
  component: Controls,
};

export default {
  bridge: 1,
  pages: [controlsPage],
} satisfies AppContribution;
