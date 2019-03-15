#conding = utf-8

from socket import *
import threading

from time import ctime
import os

address = "127.0.0.1"
port = 8080
buff_size = 1024  # 接收从客户端发来的数据的缓存区大小
s = socket(AF_INET, SOCK_STREAM)
s.bind((address, port))
s.listen(2)     # 最大连接数


def tcp_link(sock,addr):
    while True:
        recv_data = client_sock.recv(buff_size).decode('utf-8')
        if recv_data == 'exit' or not recv_data:
            break
        send_data = '服务器接受到的客户端的数据为：' + recv_data
        client_sock.send(send_data.encode())
    client_sock.close()


while True:
    print('Waiting for connection...')
    client_sock,client_address = s.accept()
    print('connect from:',client_address)
    while True:
        # 传输数据都利用client_sock，和s无关
        t = threading.Thread(target=tcp_link, args=(client_sock, client_address))  # t为新创建的线程
        t.start()
s.close()

