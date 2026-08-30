# BooruFlow crash diagnostics on Windows

BooruFlow writes normal application logs to `var/logs/booruflow.log` and fatal
Python/native diagnostics to `var/logs/booruflow-fatal.log`. A session marker in
`var/state` allows the next launch to report an earlier unclean exit.

After a sudden disappearance, preserve both log files before restarting the
same scenario. In Windows Event Viewer, open:

`Windows Logs > Application`

Filter the current time range for event ID 1000 (`Application Error`) and 1001
(`Windows Error Reporting`). Copy:

- event time;
- faulting application and process ID;
- faulting module name and path;
- exception code;
- fault offset;
- WER report ID, if present.

If no matching Windows event exists, that fact is useful too: send the last 100
lines of both BooruFlow logs and the exact time at which the window disappeared.
