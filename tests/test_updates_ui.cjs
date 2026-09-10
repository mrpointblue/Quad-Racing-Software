const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('backend/static/updates.js', 'utf8');
const template = fs.readFileSync('backend/templates/settings.html', 'utf8');

async function render(data) {
    const elements = new Map();
    for (const match of source.matchAll(/getElementById\("([^"]+)"\)/g)) {
        assert.ok(template.includes(`id="${match[1]}"`), `Missing HTML target: ${match[1]}`);
        elements.set(match[1], {textContent: '', className: '', hidden: false, addEventListener() {}});
    }
    vm.runInNewContext(source, {
        document: {getElementById: id => elements.get(id)},
        fetch: async () => ({ok: true, json: async () => data}),
        setTimeout() {}, clearTimeout() {},
    });
    await new Promise(resolve => setImmediate(resolve));
    return id => elements.get(`software-update-${id}`);
}

(async () => {
    const base = {enabled: true, busy: false, safety: {safe: true},
        installed: {race: {revision: '123456789012345'}}, candidate: {race: {revision: 'abcdefabcdef000', channel: 'dev'}}};
    let el = await render({...base, phase: 'success', message: 'Update erfolgreich installiert. Seite neu laden.'});
    assert.equal(el('status').textContent, 'Update erfolgreich installiert.');
    assert.equal(el('status').className, 'success');
    assert.equal(el('current').textContent, '123456789012');
    assert.equal(el('backup-row').hidden, true);
    assert.equal(el('install').disabled, true);
    el = await render({...base, phase: 'ready', available: true, backup: 'update-backups/test.tar'});
    assert.equal(el('install').disabled, false);
    assert.equal(el('backup-row').hidden, false);
    el = await render({...base, phase: 'error', message: 'Registry offline', available: false});
    assert.equal(el('status').className, 'error');
    assert.equal(el('next').textContent, '—');
    el = await render({...base, phase: 'ready', available: true, safety: {safe: false, reason: 'Lauf aktiv'}});
    assert.equal(el('install').disabled, true);
    assert.equal(el('safety').hidden, false);
    el = await render({...base, phase: 'installing', busy: true});
    assert.equal(el('check').disabled, true);
    assert.equal(el('install').disabled, true);
    assert.ok(template.includes('id="accordion_software_update"'));
    assert.ok(template.includes('software_update: true'));
    console.log('Update UI: DOM targets, accordion, success, error, busy, safety and backup states passed.');
})().catch(error => {console.error(error); process.exitCode = 1;});
