# Server layout

All current website code, process controls, logs and deployment information live under the user-owned `~/clinicaltext` directory. Existing GPU runtime/model/test directories remain at their original locations and are linked from this directory, so active jobs and other server services are not relocated or restarted.

```text
~/clinicaltext/
  web/                  # this repository, including UI and GPU job worker
  runtime/              # private agent token, PID files, logs, temporary sessions
  scripts/              # start, status, stop: only this website process
  gpu-runtime -> ...    # existing isolated Ollama runtime, unchanged
  evaluations -> ...    # existing synthetic evaluation jobs, unchanged
  initial-web-snapshot/ # first experimental tunnel deployment and logs
  README.md             # exact commands and current public URL
```

The public HTTPS site serves the EMR UI and a small synthetic-job queue. The program in `web/gpu_agent.py` connects outward over HTTPS and performs actual pseudonymization/validation on the existing GPU model. The laptop is not in the serving or inference path. No incoming network port, firewall rule, nginx configuration, shared GPU daemon or host reboot is required. `scripts/start.sh` starts only the website's polling worker and its narrow supervisor. It does not restart Ollama or the physical server. A system reboot may require starting this worker again; no boot-time system service is installed by this project.
