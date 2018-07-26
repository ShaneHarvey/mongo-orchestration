var rst = new ReplSetTest({name: 'set', nodes: 2});
rst.startSet();
rst.initiate();

for (var i = 0; i < rst.nodes.length; i++) {
	rst.nodes[i].adminCommand({isMaster:1})
}

function shutdown(node) {
	jsTestLog('Sending shutdown')
	try {
		node.adminCommand({shutdown:1, force:1});
	} catch (err) {
		jsTestLog('SHUTDOWN COMMAND RESULT:' + err)
	}
}
var secondaries = rst.getSecondaries();
var primary = rst.getPrimary();
for (var i = 0; i < secondaries.length; i++) {
	shutdown(secondaries[i]);
}
// Sleep for a few seconds to cause a race between primary step down and
// shutdown command.
jsTestLog('Sleeping')
sleep(7000);
// shutdown(primary);
jsTestLog('shutdowning down primary')
run('python', 'shutdown-primary.py', ''+primary.port)
jsTestLog('Set should be shutdown')
