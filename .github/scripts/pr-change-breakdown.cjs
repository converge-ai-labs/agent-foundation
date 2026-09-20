const {createHash} = require('node:crypto');
const {categories, classify, component} = require('./pr-change-rules.cjs');
const MARKER = '<!-- pr-change-breakdown -->';
const MAX_BODY = 60000;

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

function tableRow(label, files) {
  const sum = totals(files);
  return `| ${escapeText(label)} | ${sum.count} | +${sum.additions} | -${sum.deletions} |`;
}

function report(pr, files) {
  const groups = categories.map(category => ({
    category, files: files.filter(file => classify(file.filename) === category),
  })).filter(group => group.files.length);
  const complete = files.length === pr.changed_files;
  const sum = totals(files);
  const url = `${pr.html_url}/files`;
  let body = `${MARKER}\n## Change breakdown\n\n` +
    `Head: \`${pr.head.sha}\` · Base: \`${pr.base.sha}\`\n\n`;
  if (!complete) {
    body += `**Incomplete report: GitHub returned ${files.length} of ${pr.changed_files} files. ` +
      'The files API is capped at 3,000 files. All statistics below cover returned files only.**\n\n';
  }
  body += `${sum.count} files · +${sum.additions} / -${sum.deletions}\n\n` +
    '| Category | Files | Added | Deleted |\n| --- | ---: | ---: | ---: |\n' +
    groups.map(group => tableRow(group.category, group.files)).join('\n') + '\n\n';
  const product = files.filter(file => classify(file.filename) === 'Product code');
  if (product.length) {
    body += '### Product code by component\n\n' +
      '| Component | Files | Added | Deleted |\n| --- | ---: | ---: | ---: |\n' +
      [...new Set(product.map(file => component(file.filename)))].sort()
        .map(name => tableRow(name, product.filter(file => component(file.filename) === name)))
        .join('\n') + '\n\n';
  }
  body += 'File-level classification; inline tests stay with their source file. ' +
    'Line counts are GitHub textual additions/deletions, not risk scores. ' +
    'Binary changes and pure renames may have no textual delta. ' +
    'Renames count once under the new path; deleted files use the old path.\n\n';
  let omitted = 0;
  for (const group of groups) {
    const heading = `<details>\n<summary>${escapeText(group.category)} — ${group.files.length} files</summary>\n\n`;
    const rows = [];
    for (const file of [...group.files].sort((a, b) => a.filename.localeCompare(b.filename))) {
      const anchor = createHash('sha256').update(file.filename).digest('hex');
      const name = file.previous_filename
        ? `${file.previous_filename} → ${file.filename}` : file.filename;
      const delta = file.additions || file.deletions
        ? `+${file.additions} / -${file.deletions}` : 'no textual delta';
      const row = `- [${escapeText(name)}](${url}#diff-${anchor}) — ` +
        `${escapeText(file.status)} · ${delta} · ${escapeText(component(file.filename))}\n`;
      // Leave room for closing tags, remaining category headings and the notice.
      if (body.length + heading.length + rows.join('').length + row.length < MAX_BODY - 4000) {
        rows.push(row);
      } else {
        omitted++;
      }
    }
    body += heading + rows.join('') + '\n</details>\n\n';
  }
  if (omitted) body += `${omitted} file links omitted to fit the comment; tables still include all returned files.\n\n`;
  return body + `[View all changes](${url})\n`;
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
