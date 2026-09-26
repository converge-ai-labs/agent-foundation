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
