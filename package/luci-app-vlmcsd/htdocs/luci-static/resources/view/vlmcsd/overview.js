'use strict';
'require view';
'require form';
'require rpc';
'require uci';
'require poll';
'require ui';

var getStatus = rpc.declare({ object: 'luci.vlmcsd', method: 'status', expect: {} });
var getLogs = rpc.declare({ object: 'luci.vlmcsd', method: 'logs', expect: {} });
var action = rpc.declare({ object: 'luci.vlmcsd', method: 'action', params: ['action'], expect: {} });
var selftest = rpc.declare({ object: 'luci.vlmcsd', method: 'selftest', params: ['protocol'], expect: {} });

return view.extend({
 load: function() { return Promise.all([uci.load('vlmcsd'), getStatus()]); },
 render: function(data) {
  var m = new form.Map('vlmcsd', _('Volume License Service'), _('LAN IPv4 service. DNS discovery is disabled by default.'));
  var s = m.section(form.NamedSection, 'main', 'vlmcsd');
  s.anonymous = true;
  var o = s.option(form.Flag, 'enabled', _('Enable')); o.rmempty = false;
  o = s.option(form.Value, 'interface', _('LAN interface')); o.default = 'lan'; o.rmempty = false; o.datatype = 'uciname';
  o = s.option(form.Value, 'port', _('Port')); o.datatype = 'range(1024,65535)'; o.rmempty = false;
  o = s.option(form.Value, 'max_workers', _('Maximum workers')); o.datatype = 'range(1,64)'; o.rmempty = false;
  o = s.option(form.Value, 'timeout', _('Connection timeout (seconds)')); o.datatype = 'range(1,300)'; o.rmempty = false;
  o = s.option(form.Flag, 'verbose', _('Verbose logging')); o.rmempty = false;
  o = s.option(form.Flag, 'dns', _('DNS discovery')); o.rmempty = false;
  o.description = _('Publish a managed _vlmcs._tcp record in the local DNS domain.');
  var status = E('pre', { 'style': 'white-space:pre-wrap' });
  var logs = E('pre', { 'style': 'white-space:pre-wrap;max-height:26em;overflow:auto' });
  var diagnosis = E('pre', { 'style': 'white-space:pre-wrap' });
  function showStatus(d) {
   status.textContent = [
    _('Status') + ': ' + (d.running ? _('Running') : _('Stopped')),
    _('Version') + ': ' + (d.version || '?'),
    _('Source commit') + ': ' + (d.source_commit || '?'),
    'PID: ' + d.pid + ' | ' + _('Uptime (seconds)') + ': ' + d.uptime_seconds + ' | RSS: ' + d.memory_kib + ' KiB',
    _('Listeners') + ':\n' + ((d.listeners || []).join('\n') || _('None'))
   ].join('\n');
  }
  showStatus(data[1]);
  function button(label, fn) { return E('button', { 'class': 'cbi-button cbi-button-action', 'click': ui.createHandlerFn(null, fn) }, label); }
  function refreshLogs() { return getLogs().then(function(r) { logs.textContent = r.output || _('No recent logs'); }); }
  poll.add(function() { return getStatus().then(showStatus); }, 5);
  var controls = E('div', { 'class': 'cbi-section' }, [
   E('h3', {}, _('Status')), status,
   button(_('Start'), function() { return action('start').then(function(r) { if (!r.ok) ui.addNotification(null, E('pre', {}, r.output)); return getStatus().then(showStatus); }); }),
   ' ', button(_('Stop'), function() { return action('stop').then(function(r) { if (!r.ok) ui.addNotification(null, E('pre', {}, r.output)); return getStatus().then(showStatus); }); }),
   ' ', button(_('Restart'), function() { return action('restart').then(function(r) { if (!r.ok) ui.addNotification(null, E('pre', {}, r.output)); return getStatus().then(showStatus); }); }),
   E('h3', {}, _('Protocol diagnostics')),
   E('p', {}, _('Tests send one request to this router. Status polling does not send requests.')),
   E('div', {}, [4, 5, 6].map(function(p) { return button('KMS v' + p, function() { diagnosis.textContent = _('Testing…'); return selftest(p).then(function(r) { diagnosis.textContent = (r.ok ? 'OK' : 'FAIL') + ' (' + r.duration_ms + ' ms)\n' + r.output; }); }); })),
   diagnosis, E('h3', {}, _('Recent logs')),
   button(_('Refresh logs'), refreshLogs), ' ',
   button(_('Download logs'), function() {
    return refreshLogs().then(function() {
     var a = document.createElement('a'); var url = URL.createObjectURL(new Blob([logs.textContent], { type: 'text/plain;charset=utf-8' }));
     a.href = url; a.download = 'vlmcsd.log'; a.click(); setTimeout(function() { URL.revokeObjectURL(url); }, 1000);
    });
   }), logs
  ]);
  return m.render().then(function(node) { return E('div', {}, [controls, node]); });
 }
});
