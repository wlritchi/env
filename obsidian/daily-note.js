const { MarkdownView } = require('obsidian');

const TEMPLATE_PATH = 'daily note template.md';

const view = this.app.workspace.getActiveViewOfType(MarkdownView);

const log = (editor, msg) => {
    editor.setSelection({ch: 0, line: 0});
    editor.replaceSelection(`${msg}\n`);
    editor.setSelection({ch: 0, line: 1});
};

const createIfMissing = async (vault, editor, path) => {
    try {
        if (!vault.getAbstractFileByPath(path)) {
            const template = vault.getAbstractFileByPath(TEMPLATE_PATH);
            if (template) {
                const content = await vault.cachedRead(template);
                await vault.create(path, content);
            }
        }
    } catch (e) {
        log(editor, `Error creating ${path} if missing: ${e}`);
    }
};

const fixup = async (vault, editor, path) => {
    try {
        const date = moment(path.substring(0, 10));
        const today = date.format('YYYY-MM-DD');
        const yesterday = moment(date).subtract(1, 'day').format('YYYY-MM-DD');
        const tomorrow = moment(date).add(1, 'day').format('YYYY-MM-DD');
        let automationStart = null;
        for (let i = 0; i < editor.lineCount(); i++) {
            let line = editor.getLine(i);
            let new_line = line.replace(/%%(t)oday%%/, today).replace(/%%(y)esterday%%/, yesterday).replace(/%%(t)omorrow%%/, tomorrow);
            if (line !== new_line) {
                editor.setLine(i, new_line);
            }
            if (/%%(a)utomation%%/.test(line)) {
                automationStart = {ch: 0, line: i};
            }
        }
        await createIfMissing(vault, editor, `${yesterday}.md`);
        await createIfMissing(vault, editor, `${tomorrow}.md`);
        if (automationStart) {
            let line = editor.lastLine();
            const lineContent = editor.getLine(line) || '';
            const automationEnd = {ch: lineContent.length, line};
            editor.replaceRange('', automationStart, automationEnd);
        }
    } catch (e) {
        log(editor, `Error running fixup: ${e}`);
    }
};

if (view) {
    const path = view.file.path;
    if (/^\d{4}-\d{2}-\d{2}\.md$/.test(path)) {
        setTimeout(function() {
            fixup(this.app.vault, view.editor, path);
        }, 0);
    }
}
