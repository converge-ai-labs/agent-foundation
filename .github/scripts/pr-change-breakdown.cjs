const {createHash} = require('node:crypto');
const {CORE, UI, categories, icons, classify, component} = require('./pr-change-rules.cjs');
const MARKER = '<!-- pr-change-breakdown -->';
const MAX_BODY = 60000;
const TABLE_HEADER = '| Files | Added | Deleted |';

// Encode untrusted paths as literal text, including Markdown and mention syntax.
function escapeText(value) {
  return [...value].map(char => /[a-zA-Z0-9 /._-]/.test(char)
    ? char : `&#${char.codePointAt(0)};`).join('');
}

function totals(files) {
  return files.reduce((sum, file) => ({
    count: sum.count + 1,
    additions: sum.additions + file.additions,
    deletions: sum.deletions + file.deletions,
  }), {count: 0, additions: 0, deletions: 0});
}

function counts(sum) {
  return `${sum.count} ${sum.count === 1 ? 'file' : 'files'} · +${sum.additions} / -${sum.deletions}`;
}

function tableRow(label, sum, bold = false) {
  return '| ' + [label, sum.count, `+${sum.additions}`, `-${sum.deletions}`]
    .map(value => bold ? `**${value}**` : value).join(' | ') + ' |';
}

function report(pr, files) {
  const groups = categories.map(category => {
    const selected = files.filter(file => classify(file.filename) === category);
    return {category, files: selected, sum: totals(selected)};
  });
  const complete = files.length === pr.changed_files;
  const sum = totals(files);
  const changedLines = sum.additions + sum.deletions;
  const share = group => changedLines
    ? `${Math.round(100 * (group.sum.additions + group.sum.deletions) / changedLines)}% of ${complete ? 'all' : 'returned'} changed lines`
    : 'No textual delta';
  const label = category => `${icons[category]} ${escapeText(category)}`;
  const core = groups.find(group => group.category === CORE);
  const ui = groups.find(group => group.category === UI);
  const url = `${pr.html_url}/files`;
  let body = `${MARKER}\n## Change overview\n\n`;
  if (!complete) {
    body += `> [!WARNING]\n> **Incomplete report: GitHub returned ${files.length} of ${pr.changed_files} files.**\n> ` +
      'The files API is capped at 3,000 files. All statistics below cover returned files only.\n\n';
  }
  body += `> [!${core.sum.count ? 'IMPORTANT' : 'NOTE'}]\n` +
    `> **${CORE} · ${counts(core.sum)}**\n>\n`;
  if (core.sum.count) {
    const names = [...new Set(core.files.map(file => component(file.filename)))].sort();
    // Keep the focus line bounded even when a PR introduces many components.
    const focus = names.slice(0, 4).map(name => escapeText(name.slice(0, 100))).join(', ');
    body += `> **Review focus:** ${focus}${names.length > 4 ? ', …' : ''} · **${share(core)}**.\n>\n`;
  } else {
    body += `> No core library or service files ${complete ? 'changed' : 'in the returned files'}.\n>\n`;
  }
  body += `> ${UI}: **${counts(ui.sum)}** · ${share(ui)}.\n\n` +
    `**${complete ? 'Total changes' : 'Returned changes'}: ${counts(sum)}**\n\n` +
    `| Area ${TABLE_HEADER}\n| :--- | ---: | ---: | ---: |\n` +
    groups.filter(group => group.sum.count).map(group =>
      tableRow(label(group.category), group.sum, group.category === CORE)).join('\n') + '\n' +
    tableRow(complete ? 'Total' : 'Returned files', sum, true) + '\n\n';

  const codeGroups = [core, ui].filter(group => group.sum.count);
  if (codeGroups.length) {
    body += `### Changes by component\n\n| Surface | Component ${TABLE_HEADER}\n` +
      '| :--- | :--- | ---: | ---: | ---: |\n';
    let omittedComponents = 0;
    for (const group of codeGroups) {
      for (const name of [...new Set(group.files.map(file => component(file.filename)))].sort()) {
        const selected = group.files.filter(file => component(file.filename) === name);
        const row = tableRow(`${label(group.category)} | ${escapeText(name)}`, totals(selected));
        if (body.length + row.length < 12000) body += row + '\n';
        else omittedComponents++;
      }
    }
    body += '\n';
    if (omittedComponents) body += `${omittedComponents} component rows omitted; area totals remain complete for returned files.\n\n`;
  }
  let omitted = 0;
  for (const group of groups.filter(group => group.sum.count)) {
    const focus = group.category === CORE ? ' — review focus' : '';
    const heading = `<details>\n<summary><strong>${label(group.category)}${focus}</strong> · ${counts(group.sum)}</summary>\n\n` +
      '| Component / file | Change | Added | Deleted |\n| :--- | :--- | ---: | ---: |\n';
    body += heading;
    for (const file of [...group.files].sort((a, b) => a.filename.localeCompare(b.filename))) {
      const anchor = createHash('sha256').update(file.filename).digest('hex');
      const name = file.previous_filename
        ? `${file.previous_filename} → ${file.filename}` : file.filename;
      const status = escapeText(file.status) + (file.additions || file.deletions ? '' : ' · no textual delta');
      const row = `| ${escapeText(component(file.filename))} / [${escapeText(name)}](${url}#diff-${anchor}) ` +
        `| ${status} | +${file.additions} | -${file.deletions} |\n`;
      // Reserve space for remaining category headings, closing tags, and notes.
      if (body.length + row.length < MAX_BODY - 5000) body += row;
      else omitted++;
    }
    body += '\n</details>\n\n';
  }
  if (omitted) body += `${omitted} file links omitted to fit the comment; area totals include all returned files.\n\n`;
  return body + `---\n\n[View all changes](${url}) · Head: \`${pr.head.sha}\` · Base: \`${pr.base.sha}\`\n\n` +
    '<sub>File-level classification · Changed lines = additions + deletions · ' +
    'Core libraries and services appear first for review; categories describe responsibilities, not programming languages or measured risk. ' +
    'Inline tests stay with their source files. Binary changes may have no textual delta. ' +
    'Renames count once under the new path; deleted files use the old path.</sub>\n';
}

async function publish({github, context, core}) {
  const params = {...context.repo, pull_number: context.payload.pull_request.number};
  const {data: pr} = await github.rest.pulls.get(params);
  if (pr.state !== 'open') return;
  const files = await github.paginate(github.rest.pulls.listFiles, {...params, per_page: 100});
  const comments = await github.paginate(github.rest.issues.listComments, {
    ...context.repo, issue_number: pr.number, per_page: 100,
  });
  const body = report(pr, files);
  // A push/base update during pagination must not publish mixed revisions.
  const {data: current} = await github.rest.pulls.get(params);
  if (current.state !== 'open' || current.head.sha !== pr.head.sha || current.base.sha !== pr.base.sha) {
    core.notice('PR changed during collection; the next event will refresh the report.');
    return;
  }
  const previous = comments.find(comment => comment.user?.login === 'github-actions[bot]'
    && comment.body?.startsWith(MARKER));
  if (previous) {
    if (previous.body !== body) await github.rest.issues.updateComment({
      ...context.repo, comment_id: previous.id, body,
    });
  } else {
    await github.rest.issues.createComment({...context.repo, issue_number: pr.number, body});
  }
}

module.exports = {report, publish};
