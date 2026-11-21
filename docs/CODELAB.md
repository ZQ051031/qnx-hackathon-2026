# Porting a Linux C project to QNX 8.0 with qnxport

**Goal:** take a Linux event-loop example, remove its dependency on epoll, preserve the socket registration as a portable readiness check, and prepare a QNX 8.0 scaffold. The exercise runs on Linux. You need Python 3.10+, and optionally a Linux C compiler to smoke-test the rewritten example. The last step shows licensed QNX SDK commands you can run separately.

## 1. Scan the original

From the qnxport checkout:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
work=$(mktemp -d)
cp -R examples/linux-only "$work/port-demo"
qnxport doctor
qnxport scan "$work/port-demo" --fail-on none
```

With no SDP installed, doctor says so and returns success. The scan reports **QNX001** for epoll, **QNX010–012** for three Linux-style link libraries, and **QNX013** as a reminder to link the QNX socket library. No source was compiled by the scanner.

## 2. Implement the portable readiness check

Replace `$work/port-demo/main.c` with:

```c
#include <poll.h>
#include <stdio.h>
#include <sys/socket.h>
#include <unistd.h>

int main(void)
{
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd == -1) {
        perror("socket");
        return 1;
    }
    struct pollfd watch = {.fd = fd, .events = POLLIN};
    int result = poll(&watch, 1, 0);
    if (result == -1)
        perror("poll");
    close(fd);
    return result == -1;
}
```

`poll()` receives the descriptor set on each call, unlike epoll's persistent registration. This example makes a nonblocking readiness check; it does not implement a server or claim to reproduce edge-triggered behavior. In a real project review timeouts, EINTR handling, error/hangup bits, and wake-up and performance requirements. See [poll() on QNX 8.0](https://www.qnx.com/developers/docs/8.0/com.qnx.doc.neutrino.lib_ref/topic/p/poll.html).

In the fixture's `CMakeLists.txt`, replace the original `target_link_libraries` line with:

```cmake
if(CMAKE_SYSTEM_NAME STREQUAL "QNX")
    target_link_libraries(port_demo PRIVATE socket)
endif()
```

The sample doesn't actually call dlopen, start threads or create a POSIX timer, so it needs none of the three old libraries. QNX's socket API is provided by libsocket; don't add it to the Linux link.

```sh
qnxport scan "$work/port-demo" --fail-on warning
# Optional Linux-only smoke test:
cc -std=c11 -Wall -Wextra -Werror "$work/port-demo/main.c" -o "$work/port-demo-linux"
"$work/port-demo-linux"
```

Expected: **QNX013** remains because the scanner reports calls, not the success of conditional CMake linkage. The scan exits 0. The C program exits 0 if the socket and readiness check succeeded. This is not a QNX runtime test.

## 3. Check the image manifest and prepare the cross-build

```sh
qnxport buildfile lint examples/demo.build --fail-on warning
sed '/^libc\.so$/d' examples/demo.build > "$work/missing-libc.build"
qnxport buildfile lint "$work/missing-libc.build" --fail-on warning
(cd "$work" && qnxport scaffold port-demo --build-system cmake)
```

The first lint reports no findings. Removing libc triggers **IFS006** and exit 1 because the manifest lists `esh`. This illustrative manifest is independent of the demo application and has not been booted. A real BSP determines startup, console configuration and included libraries.

The scaffold produces `$work/ports/port-demo/` with two architecture variants. To take the next step, you need an already licensed QNX SDP 8.0 installation and the host's CMake. The following steps are not required to complete this host codelab and were not verified for this entry:

```sh
source ~/qnx800/qnxsdp-env.sh
qnxport doctor
make -C "$work/ports/port-demo/nto-aarch64-le" QNX_PROJECT_ROOT="$work/port-demo" install
```

The scaffold stages files under the selected variant. Deployment and target testing belong to the matching QNX 8.0 BSP or QEMU image workflow. QNX Everywhere is free for personal non-commercial use but requires a myQNX account and deployed license. Keep the Linux check in CI even if you later add an SDK-backed build to confirm the port.
