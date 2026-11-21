#include <stdio.h>
#include <sys/epoll.h>
#include <sys/socket.h>
#include <unistd.h>

int main(void)
{
    int listen_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (listen_fd == -1) {
        perror("socket");
        return 1;
    }

    int loop_fd = epoll_create1(EPOLL_CLOEXEC);
    if (loop_fd == -1) {
        perror("epoll_create1");
        close(listen_fd);
        return 1;
    }
    struct epoll_event event = { .events = EPOLLIN };
    int result = epoll_ctl(loop_fd, EPOLL_CTL_ADD, listen_fd, &event);
    if (result == -1)
        perror("epoll_ctl");
    close(loop_fd);
    close(listen_fd);
    return result == -1;
}
