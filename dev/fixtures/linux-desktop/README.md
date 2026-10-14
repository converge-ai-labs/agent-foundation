# Linux X11 desktop integration fixture

This test-only image runs a real Xvfb desktop, Openbox, an independent Tk application, native `a13n-envd`, a temporary Harness UI App, and a deterministic image-capable model. It is not a shipped Environment image or a production desktop provisioning mechanism.

From the repository root:

```bash
docker build -f dev/fixtures/linux-desktop/Dockerfile -t a13n-x11-test .
docker run --name a13n-x11-test \
  --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --cpus 2 --memory 4g --memory-swap 4g --pids-limit 256 \
  --tmpfs /tmp:rw,size=512m \
  --tmpfs /home/lab:rw,uid=10001,gid=10001,size=64m \
  a13n-x11-test
docker logs a13n-x11-test > x11-test.log
docker rm a13n-x11-test
```

The Dockerfile-specific ignore file excludes other worktrees, build output, local environments and configuration from the build context. Dependency downloads occur only during image build. Runtime networking is restricted to the container's loopback; no port is published. Do not add host display sockets, devices, home directories, Docker sockets, privileged mode, or host networking. Remove only the explicitly named test container; never use global cleanup commands.

The normal Python suite skips `test_computer_x11.py`. The image opts in with `A13N_X11_TEST=1` and `A13N_ENVD_TEST_BINARY`; these variables must not be set against a personal desktop. The fixture launches and owns display `:99` inside the disposable container.

The test exercises:

- native target discovery and JPEG capture, including actual changed pixels after a click;
- screenshot bytes in the model request, live image events, saved transcript references and authenticated image retrieval;
- click, physical key chord, wheel steps and timed drag received by a separate GUI process;
- swapped pointer and wheel mappings, so logical button/direction semantics do not accidentally depend on the default map;
- exact method negotiation: X11 does not expose literal Unicode `type_text`;
- bounded process and Session teardown.

Xvfb tests do not validate physical display drivers, compositor-specific behavior, real user keyboard races, macOS permissions, or Wayland. The backend deliberately neither mutates keyboard maps nor uses the clipboard for text entry.

## Service integration

This opt-in test is excluded from the ordinary Service CI suite, which requires zero skipped cases. Run it explicitly with this disposable desktop; do not count an unconfigured or skipped run as native Computer Use validation.

The same native desktop can serve the opt-in Service test on an explicitly published **host loopback** port. This mode runs Service and its test-owned PostgreSQL/Redis on the host; it does not mount host display sockets, credentials or application data. The credential below is public fixture data and must never be used outside this disposable loopback fixture.

After building `a13n-x11-test` above:

```bash
docker run -d --name a13n-service-x11-test \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  --cpus 2 --memory 4g --memory-swap 4g --pids-limit 256 \
  --tmpfs /tmp:rw,size=512m \
  --tmpfs /home/lab:rw,uid=10001,gid=10001,size=64m \
  -p 127.0.0.1::8888 \
  a13n-x11-test sh /app/dev/fixtures/linux-desktop/serve.sh
docker port a13n-service-x11-test 8888
# Use the loopback port printed above; wait for the fixture daemon to start.
A13N_SERVICE_X11_ENDPOINT=http://127.0.0.1:PORT \
  uv run --locked pytest packages/a13n-service/tests/environment_providers/test_computer_x11.py -v
docker stop a13n-service-x11-test
docker rm a13n-service-x11-test
```

Start a fresh container, or restart this exact test container, before repeating the test: its GUI state is intentionally changed. Do not point the test at a personal desktop. The test performs actual native actions through Service and Harness with deterministic model responses. It checks that an accepted observation-only Run retains its ceiling after a mount edit, later Runs can interact, exact X11 capabilities remove unsupported text entry after activation, screenshot bytes reach the model and authenticated retained-image endpoint, and the independent GUI records the click and key with changed pixels. It activates the window before clicking the target control; a native input receipt proves dispatch, not that an application consumed the gesture. This is not a benchmark or a validation of autonomous model reasoning, arbitrary Unicode input, physical displays or other operating systems.
