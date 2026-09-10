const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('backend/templates/monitor.html', 'utf8');
const start = source.indexOf('                const orientation = data.display_setting.display_orientation;');
const end = source.indexOf('                document.body.classList.toggle("monitor-theme-light"', start);
assert.ok(start >= 0 && end > start);
const classes = new Set();
const document = {body: {classList: {toggle(name, active) {active ? classes.add(name) : classes.delete(name);}}}};
for (const orientation of ['portrait', 'portrait_left', 'landscape', 'portrait_left', 'portrait', null]) {
    vm.runInNewContext(source.slice(start, end), {document, data: {display_setting: {display_orientation: orientation}}});
    assert.equal(classes.has('monitor-portrait'), ['portrait', 'portrait_left'].includes(orientation));
    assert.equal(classes.has('monitor-portrait-left'), orientation === 'portrait_left');
    assert.equal(classes.has('monitor-landscape'), !['portrait', 'portrait_left'].includes(orientation));
}
const picker = fs.readFileSync('backend/templates/monitor_output.html', 'utf8');
assert.ok(picker.includes('Gedreht links') && picker.includes('Gedreht rechts'));
assert.ok(!picker.includes('Gedreht 90°'));
console.log('Monitor rotation: all live transitions, old right rotation and both labels verified.');
