const fs = require('node:fs');
const path = require('node:path');

const marker = '<!-- a13n-envd-size -->';
const targets = new Map([
  ['x86_64-unknown-linux-gnu', 'Linux x86_64'],
  ['aarch64-unknown-linux-gnu', 'Linux ARM64'],
  ['x86_64-apple-darwin', 'macOS x86_64'],
  ['aarch64-apple-darwin', 'macOS ARM64'],
  ['x86_64-pc-windows-msvc', 'Windows x86_64'],
  ['aarch64-pc-windows-msvc', 'Windows ARM64'],
]);

function render(reports, run, repo) {
  const rows = [];
  const keys = new Set();
  const budgets = new Set();
  let failed = 0;
  for (const report of reports) {
    if (!targets.has(report.target) || !['default', 'vm'].includes(report.variant)) throw new Error('Invalid build identity');
    for (const field of ['binary_bytes', 'archive_bytes', 'binary_budget', 'archive_budget']) {
      if (!Number.isSafeInteger(report[field]) || report[field] <= 0) throw new Error('Invalid size');
    }
    const key = `${report.target}/${report.variant}`;
    if (keys.has(key)) throw new Error('Duplicate build report');
    keys.add(key);
    const mib = value => (value / 1048576).toFixed(2);
    const passed = report.binary_bytes <= report.binary_budget && report.archive_bytes <= report.archive_budget &&
      (report.variant !== 'vm' || report.microsandbox_copies === 1);
    if (!passed) failed++;
    const runtime = report.variant === 'vm' && report.microsandbox_copies !== 1
      ? ` — ${Number(report.microsandbox_copies)} runtime copies (expected 1)` : '';
    const build = report.variant === 'vm' ? 'VM' : 'Default';
    budgets.add(`${build}: ${mib(report.binary_budget)} / ${mib(report.archive_budget)} MiB`);
    rows.push(`| ${targets.get(report.target)} | ${build} | ${mib(report.binary_bytes)} | ${mib(report.archive_bytes)} | ${passed ? 'Pass' : '**FAIL**'}${runtime} |`);
  }
  rows.sort();
  const summary = failed
    ? `**${failed} of ${rows.length} reported builds failed.**`
    : `**${rows.length} build${rows.length === 1 ? '' : 's'} within budget.**`;
  return [marker, '## Envd release size', '', summary, '',
    '| Platform | Build | Binary (MiB) | Archive (MiB) | Result |',
    '| --- | --- | ---: | ---: | --- |', ...rows, '',
    ...(rows.length < 6 ? [`${rows.length}/6 build reports available for this attempt; see CI for missing measurements. Successful jobs from earlier attempts are not remeasured by a failed-jobs-only rerun.`, ''] : []),
    '<details>', '<summary>Budgets and build details</summary>', '',
    `Limits (binary / archive): ${[...budgets].sort().join('; ')}.`, '',
    'Measured from the release binaries used by native tests. VM builds must contain exactly one embedded Microsandbox runtime archive.', '',
    '</details>', '',
    `\`${run.head_sha.slice(0, 7)}\` · [CI run](https://github.com/${repo.owner}/${repo.repo}/actions/runs/${run.id}) (attempt ${run.run_attempt}).`,
  ].join('\n');
}

async function post({github, context, core, directory = 'size-reports'}) {
  const run = context.payload.workflow_run;
  if (run.event !== 'pull_request') return;
  // Resolve authority from GitHub metadata, never from an artifact's PR number.
  const pulls = await github.paginate(github.rest.pulls.list, {...context.repo, state: 'open', per_page: 100});
  const pr = pulls.find(pr => pr.head.sha === run.head_sha && pr.head.repo?.id === run.head_repository?.id);
  if (!pr) { core.info('No current PR head matches this run'); return; }
  const reports = [];
  if (fs.existsSync(directory)) {
    for (const entry of fs.readdirSync(directory, {withFileTypes: true})) {
      if (!entry.isDirectory() || !entry.name.startsWith('envd-size-') ||
        !entry.name.endsWith(`-attempt-${run.run_attempt}`)) continue;
      const file = path.join(directory, entry.name, 'report.json');
      if (!fs.existsSync(file)) continue;
      if (!fs.lstatSync(file).isFile() || fs.statSync(file).size > 16384) throw new Error('Invalid report file');
      reports.push(JSON.parse(fs.readFileSync(file, 'utf8')));
    }
  }
  if (!reports.length) { core.info('No size measurements produced'); return; }
  const body = render(reports, run, context.repo);
  const comments = await github.paginate(github.rest.issues.listComments, {...context.repo, issue_number: pr.number, per_page: 100});
  const previous = comments.find(comment => comment.user?.login === 'github-actions[bot]' && comment.body?.startsWith(marker));
  // An older workflow attempt must not overwrite a newer report for the same head.
  if (previous) {
    const match = previous.body.match(/actions\/runs\/(\d+)\) \(attempt (\d+)\)/);
    if (match && (Number(match[1]) > run.id || (Number(match[1]) === run.id && Number(match[2]) > run.run_attempt))) return;
  }
  const current = (await github.rest.pulls.get({...context.repo, pull_number: pr.number})).data;
  if (current.head.sha !== run.head_sha) return;
  if (previous) await github.rest.issues.updateComment({...context.repo, comment_id: previous.id, body});
  else await github.rest.issues.createComment({...context.repo, issue_number: pr.number, body});
}
module.exports = {post, render};
