from __future__ import *

import sys
from enum import Enum

from Helper.HstMessage import *
from Helper.HstSendWindow import *

sys.path.append("..")
from Helper import *
from Utils.Log import *

from socket import *
import json
import os
import time
import struct
from progressbar import *
import threading
import random

#客户端状态码
class State(Enum):
    CLOSED = 0
    SYN_SEND = 1
    ESTABLISH = 2
    CLOSN_WAIT = 3
    LAST_ACK = 4  # TCP建立链接过程中的等待原来的发向远程TCP的连接中断请求的确认状态

class HstClient:
    def __init__(self, host, port, buffersize):
        self.host = host
        self.port = port
        self.buffersize = buffersize
        self.udpClient = socket(AF_INET, SOCK_DGRAM)  # AF_INET使用的是IPv4协议，SOCK_DGRAM是字节包。
        self.state = State.CLOSED
        self.lock = threading.Lock()   # 能锁住变量的更新, 让它按预定的方式进行更新和交流

        #  用于三次握手
        self.server_isn = 1  # 服务器端自己的ISN序列号, 发送完毕后，客户端进入 SYN_SEND 状态
        self.client_isn = random.randint(0, 1000)  # 从空闲的地址池中选择一个端口，新开一个数据线程

        #  拥塞窗口CWnd
        #  避免网络拥堵
        self.cwnd = 1
        self.rwnd = 1000  # 接收窗口

        # 为了防止cwnd增加过快而导致网络拥塞需要设置慢启动阈值
        # 如果窗口大于ssthresh，那么就执行线性增窗的拥塞避免阶段，否则执行慢启动.
        # 阈值设置为发送窗口的一半
        self.ssthresh = 12

        # 定时器
        # 如果当定时器溢出时还没收到确认，就重传该数据
        self.timer = None
        self.TimeoutInterval = 0.01

    def start(self, HstCmd, filename):
        if HstCmd == "UPLOAD":
            self.ControlHandshake()
            self.UploadFile(filename)
        elif HstCmd == "DOWNLOAD":
            self.ControlHandshake()
            self.DownloadFile(filename)
        elif HstCmd == "LIST":
            print(os.listdir())
        elif HstCmd == "CD":
            print(os.chdir("../"))
        elif HstCmd == "CWD":
            print(os.getcwd())
        else:
            print(">>>>>找不到该命令")

    def ControlHanShake(self):
        con_time = 0
        self.udpClient.settimeout(2)
        while True:
            # 发生请求获取分配窗口
            request = HstMessage(getport = 1)
            request.getport = 1
            self.udpClient.sendto(request.pack(), (self.host, self.port))  # 发送UDP数据，将数据发送到套接字，用（ipaddr，port）的元组，指定远程地址。
            try:
                message = self.udpClient.recv(2048)
            except Exception as e:
                con_time += 1
                if con_time == 10:
                    log_error("连接失败，请检查网络/服务器.")
                    return False
                log_warn("连接超时，正在进行第" + con_time + "次重连")
                continue
            message = HstMessage.unpack(message)
            if message.getport == 1:
                data = json.loads(message.content[:message.content_size].decode("utf-8"))
                self.port = data["port"]
                return True

    def handshake(self, HstCmd, filename, filesize = 0):
        log_info("开始连接服务器。。。")
        self.fileInfo = {
            "filename": filename,
            "filesize": filesize,
            "HstCmd"  : HstCmd
        }
        fileInfojson = json.dumps(self.fileInfo).encode("utf-8")
        message = HstMessage(SYN = 1, seqnum = self.client_isn, content_size = len(fileInfojson), content = fileInfojson)

        self.udpClient.sendto(message.pack(), (self.host, self.port))
        self.state = State.SYN_SEND
        threading.Timer(0.2,self.handshakeTimer, [self.state, message, 0]).start()
        log_info("发送第一次握手报文")

        while True:
            try:
                res = self.udpClient.recv(2048)
            except Exception as e:
                log_warn("接收回应报文超时")
                if self.state == State.CLOSED:
                    log_error("连接失败")
                    break
                continue
            res = HstMessage.unpack(res)
            if self.state == State.SYN_SEND and res.SYN == 1 and res.ACK == 1 and res.acknum == self.client_isn + 1:
                log_info("收到第二次握手响应报文")
                if self.fileInfo["HstType"] == "DOWNLOAD":
                    self.fileInfo = json.loads(res.content[:res.content_size].decode("utf-8"))
                    if self.fileInfo["filesize"] == -1:
                        log_error("文件不存在，无法下载到本地：", self.fileInfo["filename"])
                        self.state = State.CLOSED
                        return
                self.server_isn = res.seqnum
                reply_message = HstMessage(SYN = 0, ACK = 1, seqnum = self.client_isn + 1, acknum = self.server_isn + 1)
                self.udpClient.sendto(reply_message.pack(), (self.host, self.port))
                self.state = State.ESTABLISH
                log_info("发送第三次握手报文")
                log_info("连接建立完毕")
                break

    def handshakeTimer(self, state, message, timeout):
        if(state == self.state):
            if timeout == 10:
                log_error("第一次握手失败")
                self.state = State.CLOSED
                return
            if(state == State.SYN_SEND):
                self.udpClient.sendto(message.pack(), (self.host, self.port))
                threading.Timer(0.2, self.handshakeTimer(), [self.state, message, timeout + 1]).start()
                log_warn("第一次握手超时，正在重新发送第一次握手报文，当前超时次数:", timeout + 1)


    def UploadFile(self, filepath):
        try:
            filename = os.path.basename(filepath)
            filesize = os.path.getsize(filepath)
        except Exception as e:
            log_error("文件不存在/打开错误:", filepath)
            return
        # 发起握手建立连接
        self.handshake("UPLOAD", filename, filepath)
        # 握手完毕未进入连接状态，退出
        if self.state != State.ESTABLISH:
            log_error("连接建立失败，无法上传文件")
            self.state = State.CLOSED
            return

        self.send_window = HstSendWindow(0, self.rwnd)

        # 应用端持续读取数据填入发送窗口
        with open(filepath, 'rb') as f:
            log_info("开始传送文件", filename)
            threading.Thread(target = self.recvACK, args = (filesize,)).start()
            while filesize > 0:
                if(self.send_window.isFull()):
                    time.sleep(0.00001)
                    continue
                if filesize >= self.buffersize:
                    content = f.read(self.buffersize)  # 每次读取出来的文件内容
                    self.lock.acquire()
                    self.send_window.append(content)
                    self.lock.release()
                    filesize -= self.buffersize
                else:
                    content = f.read(filesize)  # 最后一次读取出来的文件内容
                    self.lock.acquire()
                    self.send_window.append(content)
                    self.lock.release()
                    filesize = 0
                    break

    # 接收客户端ACK，滑动窗口
    def recvACK(self, filesize):
        self.cwnd = 1
        while True:
            self.lock.acquire()
            if self.send_window.isEmpty() == False:
                item = self.send_window.getItemToSend()
                if item != None:
                    self.lock.release()
                    break
            self.lock.release()

                        












