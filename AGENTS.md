# Work on this repository

- This project is simulation-only. Do not connect physical robot transports.
- Read `README.md`, `docs/TOOLS.md` and `docs/CALIBRATION.md` before commanding a simulation.
- Use the project `.venv`. For this workstation's ROS-contaminated shell, use `env -u PYTHONPATH PYTHONNOUSERSITE=1 .venv/bin/python ...` and the equivalent pytest command. Do not change unrelated Conda environments.
- Inspect current RGB-D and contact feedback before proposing actions. State units and frames. Preserve original URDF limits and tool0; the simulation TCP is a separate site.
- Keep Jev live decisions, deterministic baselines and privileged evaluator data clearly labelled. Do not fake API responses, attach objects to grippers, or overwrite object poses during a manipulation episode.
- Save task-level observations, action choices and outcomes. Do not claim to export private chain-of-thought.
- Credentials belong in environment variables or ignored private local files, never source, artifacts, screenshots or Git. Only send the TypeSafe key to its documented official endpoint.
- Use a new run directory for new trials. Preserve failures. Low confidence or a control failure should yield to an agent observation/replan, not silently be counted as success.
- Keep the free-form interactive agent mode distinct from the finite demonstration playbook. This repository does not call a GPT API.
