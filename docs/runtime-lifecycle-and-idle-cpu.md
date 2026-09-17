# Runtime lifecycle and idle-CPU audit

## Process ownership

Image Analysis owns one `QProcess`. A second start is rejected while its state is
not `NotRunning`. Each launch has a session ID and passes the application PID to
the worker. On Windows the worker opens a `SYNCHRONIZE` handle to that exact
parent process and exits when the handle is signalled. This remains safe if the
numeric PID is later reused.

Normal shutdown sends `STOP` first, waits 3 seconds, then uses `terminate()` and
finally `kill()` with bounded waits. Recycling is scheduled only from the
`finished` signal, after the old process has been confirmed stopped.

## Qt timer inventory

| Owner | Interval | Type | Idle behaviour | Callback |
|---|---:|---|---|---|
| Image Analysis controller | 500 ms active / 3000 ms idle | repeating | low-frequency; queue widgets update only when their signature changes | pipeline/queue state refresh |
| Tagging controller | shares Image Analysis timer | signal connection | returns immediately without a selected post; refreshes only on state-signature change | selected analysis state |
| Review autocomplete | configured by page | single-shot | stopped | autocomplete request |
| Wiki preview | 180 ms | single-shot | stopped | render edited preview |
| Wiki autosave | 900 ms | single-shot | stopped | save changed draft |
| Review process cancellation | 3000 ms | single-shot | absent | kill fallback |
| Review page continuation | 350 ms | single-shot | absent | next page |
| Image worker recycle | 250 ms | single-shot | absent | start only after confirmed exit |
| Model install restart | 500 ms | single-shot | absent | worker restart |

Similar Artists, Library Indexer, Remote Discovery, Task Center and the log view
have no repeating timer. They update through Qt signals. Library indexing and
embedding work run in `QThread`; the main thread only formats progress signals.

## Manual verification

1. Start BooruFlow and wait for `Ready pid=...` in the persistent log.
2. Leave it idle for 60 seconds and record CPU time for the application PID and
   worker PID at the beginning and end.
3. Confirm there is one command line containing
   `-m booruflow.worker.image_analysis` for this application session.
4. Close normally; after five seconds confirm neither PID exists.
5. Start again, note both PIDs, forcibly end only the application PID, and
   confirm the worker exits within about two seconds.
6. During a small fixture index, verify Scan/Metadata, Author_ID, OpenCLIP and
   artist-profile phases, the current file, numeric count, percentage and bar.

Do not use a production-size library for this verification.
