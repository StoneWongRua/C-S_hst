#conding = utf-8

from socket import *
address = '127.0.0.1'  # 服务器的ip地址
port = 8080  # 服务器的端口号
buff_size = 1024  # 接收数据的缓存大小
s = socket(AF_INET, SOCK_STREAM)
s.connect((address,port))
while True:
    print("与服务器成功建立连接!")

    while True:
        send_data = input('>>>：')
        if send_data == 'exit':
            break
        s.send(send_data.encode())
        recv_data=s.recv(buff_size).decode('utf-8')
        print(recv_data)
s.close()
