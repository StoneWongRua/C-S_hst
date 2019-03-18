from __future__ import *

import sys
from enum import Enum

from Helper.HstMessage import *
from Helper.HstSendWindow import *

sys.path.append("..")
sys.path.append(r"c:\programdata\miniconda3\lib\site-packages")
from Helper import *
from Utils.Log import *
import progressbar

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
        else:
            print(">>>>>找不到该命令")

    def start_cmd(self, HstCmd):
        if HstCmd == "LIST":
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

        first_message = HstMessage(seqnum=item.seqnum, content_size=len(item.content), content=item.content)
        self.udpClient.sendto(first_message.pack(), (self.host, self.port))
        # 设置超时定时器
        self.timer = threading.Timer(self.TimeoutInterval, self.TimeoutAndReSend)
        self.timer.start()

        self.origin_time = time.time()
        self.last_time = time.time()
        self.last_recvsize = 0
        self.compute_result = 0
        self.total_result = 0

        recvSize = 0
        while(True):
            # 接收信息
            try:
                message = self.udpClient.recv(2048)
            except Exception as e:
                if filesize == recvSize:
                    progressbar.pbar.finish()
                    log_info("服务器端接收完毕")
                    self.timer.cancel()
                else:
                    log_error("超时重连失败")
                    self.timer.cancel()
                break
            message = HstMessage.unpack(message)
            acknum = message.acknum

            self.lock.acquire()
            # 更新滑动窗口
            if self.cwnd < self.ssthresh:
                self.cwnd += 1
            else:
                self.cwnd += 1/int(self.cwnd)
            # 更新rwnd
            self.send_window.ACKseqnum(acknum)
            self.rwnd = message.rwnd
            self.send_window.rwnd = message.rwnd
            if self.send_window.getACKTimeBySeqnum(acknum) == 4:
                # 三次冗余进行重传，同时更新cwnd和ssthresh
                self.ssthresh = self.cwnd / 2
                self.cwnd = self.cwnd/2 + 3
                r_content = self.send_window.getContentBySeqnum(acknum)
                r_message = HstMessage(seqnum=acknum, content_size=len(r_content), content=r_content)
                self.udpClient.sendto(r_message.pack(), (self.host, self.port))
                # 重新设置超时定时器
                self.timer.cancel()
                self.timer = threading.Timer(self.TimeoutInterval, self.TimeOutAndReSend)
                self.timer.start()
            elif self.send_window.getACKTimeBySeqnum(acknum) == 1:
                # 首次接收到，send_base改变
                recvSize += self.send_window.updateSendBase(acknum)
                List = self.send_window.getSendList(self.cwnd)
                for item in List:
                    message = HstMessage(seqnum=item.seqnum, content_size=len(item.content), content=item.content)
                    self.udpClient.sendto(message.pack(), (self.host, self.port))
                # 重新设置定时器
                self.timer.cancel()
                self.timer = threading.Timer(self.TimeoutInterval, self.TimeOutAndSend)
                self.timer.start()
            elif self.send_window.getACKTimeBySeqnum(acknum) == -1:
                recvSize = filesize
                self.UploadProgress(recvSize, filesize)
                log_info("服务器接收完毕")
                self.timer.cancel()
                self.lock.release()
                break

            self.lock.release()
            self.UpLoadProgress(recvSize, filesize)

            if filesize == recvSize:
                progressbar.pbar.finish()
                log_info("服务端接收完毕")
                self.timer.cancel()
                break

    def DownLoadProgress(self, recvSize, filesize):
        time_change = time.time() - self.last_time
        size_change = recvSize - self.last_recvsize
        if(time_change >= 0.7):
            self.last_time = time.time()
            self.last_recvsize = recvSize
            self.compute_result = size_change/time_change/1024
            self.total_result = recvSize/(time.time() - self.origin_time)/1024
        print('\r%d/%d  已经下载： %d%%  当前下载速度： %d kb/s  平均下载速度： %d kb/s' % \
            (recvSize, filesize, int(recvSize/filesize*100), self.compute_result, self.total_result), end='')
        if recvSize == filesize:
            print("")

    # 定时器，超时重传，必定重传的是send_base
    def TimeOutAndReSend(self):
        self.lock.acquire()
        self.ssthresh = self.cwnd/2
        self.cwnd = 1
        seqnum = self.send_window.send_base
        content = self.send_window.getContentBySeqnum(seqnum)
        self.lock.release()
        if content == None:
            return
        message = HstMessage(seqnum=seqnum, content_size=len(content), content=content)
        self.udpClient.sendto(message.pack(), (self.host, self.port))
        self.timer.cancel()
        self.timer = threading.Timer(self.timer.interval*2, self.TimeOutAndReSend)
        self.timer.start()

    def DownloadFile(self, filename):
        # 发起握手连接
        self.handshake("DOWNLOAD", filename)
        # 握手完毕未进入连接建立状态，退出
        if self.state != State.ESTABLISH:
            log_error("连接建立失败，无法下载文件")
            self.state = State.CLOSED
            return
        filename = self.fileInfo["filename"]
        filesize = self.fileInfo["filesize"]

        self.recv_base = 0  # 当前窗口基序号
        self.N = 1000  # 窗口大小
        self.window = []  # 接收方窗口
        for i in range(self.N):
            self.window.append(None)

        self.origin_time = time.time()
        self.last_time = time.time()
        self.last_recvsize = 0
        self.compute_result = 0
        self.total_result = 0
        recvsize = 0

        log_info("开始接收文件 %s" % (filename))
        with open(filename, 'wb') as f:
            self.udpClient.settimeout(2)
            while True:
                try:
                    message = self.udpClient.recv(2048)
                except Exception as e:
                    if(recvsize == filesize):
                        log_info("接收完毕，连接断开")
                    else:
                        log_error("连接已断开")
                    break
                message = HstMessage.unpack(message)
                seqnum = message.seqnum
                content = message.content
                content_size = message.content_size
                if(seqnum >= self.recv_base and seqnum < self.recv_base + self.N):
                    self.window[seqnum - self.recv_base] = content[:content_size]
                while self.window[0] != None:
                    f.write(self.window[0])
                    recvsize += len(self.window[0])
                    self.DownLoadProgress(recvsize, filesize)
                    self.window.pop(0)
                    self.window.append(None)
                    self.recv_base += 1

                rwnd = 0
                for item in self.window:
                    if item == None:
                        rwnd += 1
                    response = HstMessage(ACK = 1, seqnum=seqnum, rwnd=rwnd, acknum=self.recv_base)

                if(seqnum <= self.recv_base + self.N):
                    self.udpClient.sendto(response.pack(), (self.host, self.port))

    # 显示上传进度函数
    def UpLoadProgress(self, recvSize, filesize):
        time_change = time.time() - self.last_time
        size_change = recvSize - self.last_recvsize
        if(time_change >= 0.7):
            self.last_time = time.time()
            self.last_recvsize = recvSize
            self.compute_result = size_change/time_change/1024
            self.total_result = recvSize / (time.time() - self.origin_time) / 1024
        print('\r%d/%d  已经上传： %d%%  当前上传速度： %d kb/s  平均上传速度： %d kb/s' % \
              (recvSize, filesize, int(recvSize / filesize * 100), self.compute_result, self.total_result), end='')
        if recvSize == filesize:
            print("")

def getHelp():
        tip = "指令格式：\n" + \
              "  发送文件: HST lsend myserver mylargefile\n" + \
              "  下载文件: HST lget myserver mylargefile\n" + \
              "参数设置：\n" + \
              "  myserver：url地址或者ip地址\n" + \
              "  mylargefile： 文件路径"
        print('\033[33m%s' % tip)


if __name__ == '__main__':
    if len(sys.argv) != 5:
        getHelp()
    else:
        if sys.argv[1] != "HST":
            getHelp()
        else:
            if sys.argv[2] == "lsend":
                IP = sys.argv[3]
                filename = sys.argv[4]
                client = HstClient("127.0.0.1", 12345, 1024)
                client.start("UPLOAD", filename)
            elif sys.argv[2] == "lget":
                IP = sys.argv[3]
                filename = sys.argv[4]
                client = HstClient("127.0.0.1", 12345, 1024)
                client.start("DOWNLOAD", filename)
            elif sys.argv[2] == "lget":
                IP = sys.argv[3]
                filename = sys.argv[4]
                client = HstClient("127.0.0.1", 12345, 1024)
                client.start("DOWNLOAD", filename)
            else:
                getHelp()
    cmd = input(">>>>>>")
    if cmd == "up":
        IP = "127.0.0.1"
        filename = input("input the filename which you want to upload:")
        client = HstClient("127.0.0.1", 12345, 1024)
        client.start("UPLOAD", filename)
    elif cmd == "down":
        IP = "127.0.0.1"
        filename = input("input the filename which you want to upload:")
        client = HstClient("127.0.0.1", 12345, 1024)
        client.start("DOWNLOAD", filename)
    elif cmd == "list":
        client.start_cmd("LIST")
    elif cmd == "cd":
        client.start_cmd("CD")







                        












