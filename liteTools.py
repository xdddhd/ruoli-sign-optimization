import time
from typing import Sequence
import requests
import yaml
import math
import random
import os
import sys
from Crypto.Cipher import AES
from pyDes import des, CBC, PAD_PKCS5
import base64
import hashlib
from urllib import parse
import re
import json
import imghdr
from requests_toolbelt import MultipartEncoder
import datetime

import checkRepositoryVersion


class GlobalData:
    entrance = "unknown"
    launchData = {}
    startTime = time.time()
    config = {}
    msgOut = None  # FileOut类型变量, 用于控制系统print输出

    @staticmethod
    def initInMainHead():
        GlobalData.loadConfig()
        # 设置print输出
        logDir = GlobalData.config.get('logDir')
        if type(logDir) == str and GlobalData.entrance == "__main__":
            try:
                logDir = os.path.join(logDir, TT.formatStartTime(
                    "LOG#t=%Y-%m-%d--%H-%M-%S##.txt"))
                msgOut = FileOut(logDir)
            except Exception() as e:
                LL.log(2, f"文件输出启用失败, 错误信息: [{e}]")
                msgOut = FileOut(None)
        else:
            msgOut = FileOut(None)
        GlobalData.msgOut = msgOut
        msgOut.start()

    @staticmethod
    def loadConfig():
        '''配置文件载入函数'''
        try:
            config = DT.loadYml('config.yml')
        except Exception as e:
            errmsg = f"""读取配置文件出错
请尝试检查配置文件(建议下载VSCode并安装yaml插件进行检查)
错误信息: {e}"""
            LL.log(4, ST.notionStr(errmsg))
            raise e
        # 全局配置初始化
        defaultConfig = {
            'delay': (5, 10),
            'locationOffsetRange': 50,
            "shuffleTask": False
        }
        defaultConfig.update(config)
        config.update(defaultConfig)

        # 用户配置初始化
        if config['shuffleTask']:
            LL.log(1, "随机打乱任务列表")
            random.shuffle(config['users'])
        for user in config['users']:
            LL.log(1, f"正在初始化{user['username']}的配置")
            user: dict
            # 初始化静态配置项目
            defaultConfig = {
                'remarkName': '默认备注名',
                'model': 'OPPO R11 Plus',
                'appVersion': '9.0.14',
                'systemVersion': '4.4.4',
                'systemName': 'android',
                "signVersion": "first_v3",
                "calVersion": "firstv",
                'taskTimeRange': "1-7 1-12 1-31 0-23 0-59",
                'getHistorySign': False,
                'title': 0,
                'signLevel': 1,
                'abnormalReason': "回家",
                'qrUuid': None
            }
            defaultConfig.update(user)
            user.update(defaultConfig)

            # 任务进度控制
            if TT.isInTimeList(user['taskTimeRange']):
                user['taskStatus'] = SignTaskStatus(0)
            else:
                user['taskStatus'] = SignTaskStatus(201, '该任务不在执行时间')
            user['userHashId'] = HSF.strHash(
                user.get('schoolName', '')+user.get('username', ''), 256)

            # 用户设备ID
            user['deviceId'] = user.get(
                'deviceId', RT.genDeviceID(user.get('schoolName', '')+user.get('username', '')))

            # 用户代理
            user.setdefault('proxy')
            user['proxy'] = ProxyGet(user['proxy'])

            # 坐标随机偏移
            user['global_locationOffsetRange'] = config['locationOffsetRange']
            if 'lon' in user and 'lat' in user:
                user['lon'], user['lat'] = RT.locationOffset(
                    user['lon'], user['lat'], config['locationOffsetRange'])
        GlobalData.config = config
        return config


class reqSession(requests.Session):
    '''requests.Session的子类, 增添了请求的默认超时时间'''

    def request(self, *args, **kwargs):
        kwargs.setdefault('timeout', (10, 30))
        return super(reqSession, self).request(*args, **kwargs)


class FileOut:
    '''
    代替stdout和stderr, 使print同时输出到文件和终端中。
    start()方法可以直接用自身(self)替换stdout和stderr
    close()方法可以还原stdout和stderr
    '''
    stdout = sys.stdout
    stderr = sys.stderr

    def __init__(self, logPath: str = None):
        '''
        初始化
        :params logDir: 输出文件(如果路径不存在自动创建), 如果为空则不输出到文件
        '''
        self.log = ""  # 同时将所有输出记录到log字符串中
        if logPath:
            logDir = os.path.dirname(os.path.abspath(logPath))
            if not os.path.isdir(logDir):
                os.makedirs(logDir)
            self.logFile = open(logPath, "w+", encoding="utf-8")
        else:
            self.logFile = None

    def start(self):
        '''开始替换stdout和stderr'''
        sys.stdout = self
        sys.stderr = self

    def write(self, str_):
        r'''
        :params str: print传来的字符串
        :print(s)等价于sys.stdout.write(s+"\n")
        '''
        str_ = str(str_)
        self.log += str_
        if self.logFile:
            self.logFile.write(str_)
        FileOut.stdout.write(str_)
        self.flush()

    def flush(self):
        '''刷新缓冲区'''
        self.stdout.flush()
        if self.logFile:
            self.logFile.flush()

    def close(self):
        '''关闭'''
        if self.logFile:
            self.logFile.close()
        self.log = ""
        sys.stdout = FileOut.stdout
        sys.stderr = FileOut.stderr


class TaskError(Exception):
    '''目前(配置/时间/签到情况)不宜完成签到任务，出现本异常不进行重试。'''

    def __init__(self, msg="目前(配置/时间/签到情况)不宜完成签到任务", code=301, taskName='', moreInfo=''):
        '''
        :code的含义
        0: 等待执行
        1: 出现错误(等待重试)
        100: 任务已被完成
        101: 该任务正常执行完成
        200: 用户设置不执行该任务
        201: 该任务不在执行时间
        300: 出错
        301: 当前情况无法完成该任务
        400: 没有找到需要执行的任务
        '''
        self.msg = str(msg)
        self.code = code
        self.taskName = taskName
        self.moreInfo = moreInfo

    def __str__(self):
        msg = f'『{self.taskName}』' if self.taskName else ''
        msg += f'{self.msg}'
        return msg


class TT:
    '''time Tools'''
    startTime = GlobalData.startTime

    @staticmethod
    def formatStartTime(format: str = "%Y-%m-%d %H:%M:%S"):
        return time.strftime(format, time.localtime(TT.startTime))

    @staticmethod
    def isInTimeList(timeRanges, nowTime: float = startTime):
        '''判断(在列表中)是否有时间限定字符串是否匹配时间
        :params timeRages: 时间限定字符串列表。
            :时间限定字符串是形如"1,2,3 1,2,3 1,2,3 1,2,3 1,2,3"形式的字符串。
            :其各位置代表"周(星期几) 月 日 时 分", 周/月/日皆以1开始。
            :可以以"2-5"形式代表时间范围。比如"3,4-6"就等于"3,4,5,6"
        :params nowTime: 时间戳
        :return bool: 在列表中是否有时间限定字符串匹配时间
        '''
        timeRanges = DT.formatStrList(timeRanges)
        for i in timeRanges:
            if TT.isInTime(i, nowTime):
                return True
            else:
                pass
        else:
            return False

    @staticmethod
    def isInTime(timeRange: str, nowTime: float = startTime):
        '''
        判断时间限定字符串是否匹配时间
        :params timeRage: 时间限定字符串。
            :是形如"1,2,3 1,2,3 1,2,3 1,2,3 1,2,3"形式的字符串。
            :其各位置代表"周(星期几) 月 日 时 分", 周/月/日皆以1开始。
            :可以以"2-5"形式代表时间范围。比如"3,4-6"就等于"3,4,5,6"
        :params nowTime: 时间戳
        :return bool: 时间限定字符串是否匹配时间
        '''
        # 判断类型
        if type(timeRange) != str:
            raise TypeError(
                f"timeRange(时间限定字符串)应该是字符串, 而不是『{type(timeRange)}』")
        # 判断格式
        if not re.match(r"^(?:\d+-?\d*(?:,\d+-?\d*)* ){4}(?:\d+-?\d*(?:,\d+-?\d*)*)$", timeRange):
            raise Exception(f'『{timeRange}』不是正确格式的时间限定字符串')
        # 将时间范围格式化

        def formating(m):
            '''匹配a-e样式的字符串替换为a,b,c,d,e样式'''
            a = int(m.group(1))
            b = int(m.group(2))
            if a > b:
                a, b = b, a
            return ','.join([str(i) for i in range(a, b)]+[str(b)])
        timeRange = re.sub(r"(\d*)-(\d*)", formating, timeRange)
        # 将字符串转为二维整数数组
        timeRange = timeRange.split(' ')
        timeRange = [[int(j) for j in i.split(',')] for i in timeRange]
        # 将当前时间格式化为"周 月 日 时 分"
        nowTime = tuple(time.localtime(nowTime))
        nowTime = (nowTime[6]+1, nowTime[1],
                   nowTime[2], nowTime[3], nowTime[4])
        for a, b in zip(nowTime, timeRange):
            if a not in b:
                return False
            else:
                pass
        else:
            return True

    @staticmethod
    def executionSeconds(round_: int = 2):
        return round(time.time()-TT.startTime, round_)


class LL:
    '''lite log'''
    prefix = checkRepositoryVersion.checkCodeVersion()
    startTime = TT.startTime
    log_list = []
    printLevel = 0
    logTypeDisplay = ['debug', 'info', 'warn', 'error', 'critical']

    @staticmethod
    def formatLog(logType: str, args):
        '''返回logItem[时间,类型,内容]'''
        string = ''
        for item in args:
            if type(item) == dict or type(item) == list:
                string += yaml.dump(item, allow_unicode=True)+'\n'
            else:
                string += str(item)+'\n'
        return [time.time()-LL.startTime, logType, string]

    @staticmethod
    def log2FormatStr(logItem):
        logType = LL.logTypeDisplay[logItem[1]]
        return '|||%s|||%s|||%0.3fs|||\n%s' % (LL.prefix, logType, logItem[0], logItem[2])

    @staticmethod
    def log(logType=1, *args):
        '''日志函数
        logType:int = debug:0|info:1|warn:2|error:3|critical:4'''
        if not args:
            return
        logItem = LL.formatLog(logType, args)
        LL.log_list.append(logItem)
        if logType >= LL.printLevel:
            print(LL.log2FormatStr(logItem))

    @staticmethod
    def getLog(level=0):
        '''获取日志函数'''
        string = ''
        for item in LL.log_list:
            if level <= item[1]:
                string += LL.log2FormatStr(item)
        return string

    @staticmethod
    def saveLog(dir, level=0):
        '''保存日志函数'''
        if type(dir) != str:
            return

        log = LL.getLog(level)
        if not os.path.isdir(dir):
            os.makedirs(dir)
        dir = os.path.join(dir, TT.formatStartTime(
            "LOG#t=%Y-%m-%d--%H-%M-%S##.txt"))
        with open(dir, 'w', encoding='utf-8') as f:
            f.write(log)


class CpdailyTools:
    '''今日校园相关函数'''
    desKey = 'XCE927=='
    aesKey = b"SASEoK4Pa5d4SssO"
    aesKey_str = "SASEoK4Pa5d4SssO"

    @staticmethod
    def encrypt_CpdailyExtension(text, key=desKey):
        '''CpdailyExtension加密'''
        iv = b"\x01\x02\x03\x04\x05\x06\x07\x08"
        d = des(key, CBC, iv, pad=None, padmode=PAD_PKCS5)

        text = d.encrypt(text)  # 加密
        text = base64.b64encode(text)  # base64编码
        text = text.decode()  # 解码
        return text

    @staticmethod
    def decrypt_CpdailyExtension(text, key=desKey):
        '''CpdailyExtension加密'''
        iv = b"\x01\x02\x03\x04\x05\x06\x07\x08"
        d = des(key, CBC, iv, pad=None, padmode=PAD_PKCS5)

        text = base64.b64decode(text)  # Base64解码
        text = d.decrypt(text)  # 解密
        text = text.decode()  # 解码
        return text

    @staticmethod
    def encrypt_BodyString(text, key=aesKey):
        """BodyString加密"""
        iv = b'\x01\x02\x03\x04\x05\x06\x07\x08\t\x01\x02\x03\x04\x05\x06\x07'
        cipher = AES.new(key, AES.MODE_CBC, iv)

        text = CT.pkcs7padding(text)  # 填充
        text = text.encode(CT.charset)  # 编码
        text = cipher.encrypt(text)  # 加密
        text = base64.b64encode(text).decode(CT.charset)  # Base64编码
        return text

    @staticmethod
    def decrypt_BodyString(text, key=aesKey):
        """BodyString解密"""
        iv = b'\x01\x02\x03\x04\x05\x06\x07\x08\t\x01\x02\x03\x04\x05\x06\x07'
        cipher = AES.new(key, AES.MODE_CBC, iv)

        text = base64.b64decode(text)  # Base64解码
        text = cipher.decrypt(text)  # 解密
        text = text.decode(CT.charset)  # 解码
        text = CT.pkcs7unpadding(text)  # 删除填充
        return text

    @staticmethod
    def signAbstract(submitData: dict, key=aesKey_str):
        '''表单中sign项目生成'''
        abstractKey = ["appVersion", "bodyString", "deviceId", "lat",
                       "lon", "model", "systemName", "systemVersion", "userId"]
        abstractSubmitData = {k: submitData[k] for k in abstractKey}
        abstract = parse.urlencode(abstractSubmitData) + '&' + key
        abstract_md5 = HSF.strHash(abstract, 5)
        return abstract_md5

    @staticmethod
    def baiduGeocoding(address: str):
        '''地址转坐标'''
        # 获取百度地图API的密钥
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; Win64; x64; rv:50.0) Gecko/20100101 Firefox/50.0'}
        url = 'https://feres.cpdaily.com/bower_components/baidumap/baidujsSdk@2.js'
        res = requests.get(url, headers=headers, verify=False)
        baiduMap_ak = re.findall(r"ak=(\w*)", res.text)[0]
        # 用地址获取相应坐标
        url = f'http://api.map.baidu.com/geocoding/v3'
        params = {
            "output": "json", "address": address, "ak": baiduMap_ak}
        res = requests.get(
            url, headers=headers, params=params, verify=False)
        res = DT.resJsonEncode(res)
        lon = res['result']['location']['lng']
        lat = res['result']['location']['lat']
        return (lon, lat)

    @staticmethod
    def baiduReverseGeocoding(lon: float, lat: float):
        '''地址转坐标'''
        # 获取百度地图API的密钥
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; Win64; x64; rv:50.0) Gecko/20100101 Firefox/50.0'}
        url = 'https://feres.cpdaily.com/bower_components/baidumap/baidujsSdk@2.js'
        res = requests.get(url, headers=headers, verify=False)
        baiduMap_ak = re.findall(r"ak=(\w*)", res.text)[0]
        # 用地址获取相应坐标
        url = f'http://api.map.baidu.com/reverse_geocoding/v3'
        params = {
            "output": "json", "location": "%f,%f" % (lon, lat), "ak": baiduMap_ak}
        res = requests.get(url, headers=headers, params=params, verify=False)
        res = DT.resJsonEncode(res)
        address = res['result']['formatted_address']
        return address

    @staticmethod
    def uploadPicture(url, session, picBlob, picType):
        '''上传图片到阿里云oss'''
        res = session.post(url=url, headers={'content-type': 'application/json'}, data=json.dumps({'fileType': 1}),
                           verify=False)
        datas = DT.resJsonEncode(res).get('datas')
        fileName = datas.get('fileName')
        policy = datas.get('policy')
        accessKeyId = datas.get('accessid')
        signature = datas.get('signature')
        policyHost = datas.get('host')
        ossKey = f'{fileName}.{picType}'
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; Win64; x64; rv:50.0) Gecko/20100101 Firefox/50.0'
        }
        multipart_encoder = MultipartEncoder(
            fields={  # 这里根据需要进行参数格式设置
                'key': ossKey, 'policy': policy, 'AccessKeyId': accessKeyId,
                'signature': signature, 'x-obs-acl': 'public-read',
                'file': ('blob', picBlob, f'image/{picType}')
            })
        headers['Content-Type'] = multipart_encoder.content_type
        res = session.post(url=policyHost,
                           headers=headers,
                           data=multipart_encoder)
        return ossKey

    @staticmethod
    def getPictureUrl(url, session, ossKey):
        '''获取图片上传位置'''
        params = {'ossKey': ossKey}
        res = session.post(url=url, headers={'content-type': 'application/json'}, data=json.dumps(params),
                           verify=False)
        photoUrl = res.json().get('datas')
        return photoUrl


class NT:
    '''NetTools'''
    @staticmethod
    def isDisableProxies(proxies: dict):
        '''
        检查代理是否可用
        :return 如果代理正常返回0，代理异常返回1
        '''
        try:
            requests.get(url='https://www.baidu.com/',
                         proxies=proxies, timeout=10)
        except requests.RequestException as e:
            return 1
        return 0


class MT:
    '''MiscTools'''
    @staticmethod
    def geoDistance(lon1, lat1, lon2, lat2):
        '''两经纬度算距离'''
        # 经纬度转换成弧度
        lon1, lat1, lon2, lat2 = map(math.radians, [float(
            lon1), float(lat1), float(lon2), float(lat2)])
        dlon = lon2-lon1
        dlat = lat2-lat1
        a = math.sin(dlat/2)**2 + math.cos(lat1) * \
            math.cos(lat2) * math.sin(dlon/2)**2
        distance = 2*math.asin(math.sqrt(a))*6371393  # 地球平均半径，6371393m
        return distance


class PseudoRandom:
    '''随机数种子临时固定类(用于with语句)'''

    def __init__(self, seed=time.time()):
        self.seed = str(seed)
        random.seed(self.seed, version=2)

    def __enter__(self):
        return self.seed

    def __exit__(self, exc_type, exc_val, exc_tb):
        random.seed(str(time.time()), version=2)


class RT:
    '''randomTools'''
    default_offset = 50
    default_location_round = 6

    @staticmethod
    def locationOffset(lon, lat, offset=default_offset, round_=default_location_round):
        '''经纬度随机偏移(偏移不会累积)
        lon——经度
        lat——纬度
        offset——偏移范围(单位m)
        round_——保留位数
        '''
        lon = float(lon)
        lat = float(lat)
        if offset == 0:
            return (lon, lat)
        # 限定函数(经度-180~180，维度-90~90)

        def limit(n, a, b):
            if n < a:
                n = a
            if n > b:
                n = b
            return n
        # 弧度=弧长/半径，角度=弧长*180°/π，某地经度所对应的圆半径=cos(|维度|)*地球半径
        # ==纬度==
        # 偏移大小
        latOffset = offset/6371393*(180/math.pi)
        # 偏移范围
        lat_a = lat-lat % latOffset
        lat_a = limit(lat_a, -90, 90)
        lat_b = lat+0.99*latOffset-lat % latOffset
        lat_b = limit(lat_b, -90, 90)
        # 随机偏移
        lat = random.uniform(lat_a, lat_b)
        # 保留小数
        lat = round(lat, round_)

        # ==经度==
        # 偏移大小(依赖纬度计算)
        lonOffset = offset / \
            (6371393*math.cos(abs(lat_a/180*math.pi)))*(180/math.pi)
        # 偏移范围
        lon_a = lon-lon % lonOffset
        lon_b = lon+0.99*lonOffset-lon % lonOffset
        lon_a = limit(lon_a, -180, 180)
        lon_b = limit(lon_b, -180, 180)
        # 随机偏移
        lon = random.uniform(lon_a, lon_b)
        # 保留小数
        lon = round(lon, round_)

        return (lon, lat)

    @staticmethod
    def choiceFile(dir):
        '''从指定路径(路径列表)中随机选取一个文件路径'''
        if type(dir) == list or type(dir) == tuple:
            '''如果路径是一个列表/元组，则从中随机选择一项'''
            dir = random.choice(dir)
        if os.path.isfile(dir):
            '''如果路径指向一个文件，则返回这个路径'''
            return dir
        else:
            files = os.listdir(dir)
            '''如果路径指向一个文件夹，则随机返回一个文件夹里的文件'''
            if len(files) == 0:
                raise Exception("路径(%s)指向一个空文件夹" % dir)
            return os.path.join(dir, random.choice(files))

    @staticmethod
    def choiceInList(item):
        '''从列表/元组中随机选取一项'''
        if type(item) in (list, tuple):
            return random.choice(item)
        else:
            return item

    @staticmethod
    def choicePhoto(picList):
        """
        从图片(在线/本地/文件夹)文件夹中选取可用图片(优先选取在线图片)，并返回其对应的二进制文件和图片类型

        :param picList: 图片(在线/本地/文件夹)地址，可用是序列或字符串
        :param dirTimeFormat: 是否对本地地址中的时间元素格式化(使用time.strftime)
        :returns: 返回(picBlob: bytes二进制图片, picType: str图片类型)
        """
        # 格式化picList为list
        picList = DT.formatStrList(picList)
        # 打乱picList顺序
        random.shuffle(picList)

        # 根据图片地址前缀筛选出在线图片列表
        urlList = filter(lambda x: re.match(r'https?:\/\/', x), picList)
        for url in urlList:
            '''遍历url列表, 寻找可用图片'''
            # 下载图片
            LL.log(1, f'正在尝试下载[{url}]')
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/99.0.4844.74 Safari/537.36 Edg/99.0.1150.46", }
            try:
                response = requests.get(
                    url=url, headers=headers, timeout=(10, 20))
            except requests.exceptions.ConnectionError:
                LL.log(1, f'在线图片[{url}]下载失败，错误原因:\n{e}\
                    \n可能造成此问题的原因有:\
                    \n1. 图片链接失效(请自行验证链接是否可用)\
                    \n2. 图片请求超时(过几分钟再试一下)\
                    \n如持续遇到此问题，请检查该链接的有效性或移除此链接')
                continue
            picBlob = response.content
            LL.log(1, f'在线图片[{url}]下载成功')
            # 判断图片类型
            picType = imghdr.what(None, picBlob)
            if picType:
                LL.log(1, f'在线图片[{url}]属于{picType}')
                return picBlob, picType
            else:
                LL.log(1, f'在线图片[{url}]不是正常图片')
                continue

        # 根据图片地址前缀筛选出本地路径列表
        dirList = list(set(picList) - set(urlList))
        # 将被路径指向文件加入列表
        fileList = list(filter(lambda x: os.path.isfile(x), dirList))
        # 将被路径指向文件夹中的图片加入列表
        folderList = list(filter(lambda x: os.path.isdir(x), dirList))
        for folder in folderList:
            for root, _, files in os.walk(folder, topdown=False):
                for name in files:
                    fileDir = os.path.join(root, name)
                    fileDir = os.path.abspath(fileDir)
                    fileList.append(fileDir)
        # 打乱文件列表
        random.shuffle(fileList)

        for file in fileList:
            '''遍历路径列表, 寻找可用图片'''
            with open(file, 'rb') as f:
                picBlob = f.read()
            picType = imghdr.what(None, picBlob)
            if picType:
                LL.log(1, f'本地图片[{file}]属于{picType}')
                return picBlob, picType
            else:
                LL.log(1, f'本地图片[{file}]不是正常图片')
                continue

        # 如果没有找到可用图片，开始报错
        LL.log(2, '图片列表中没有可用图片')
        # 报出无效本地路径列表
        invalidPath = list(set(dirList) - set(fileList) - set(folderList))
        if invalidPath:
            LL.log(1, '无效本地路径列表', invalidPath)
        raise Exception('图片列表中没有可用图片')

    @staticmethod
    def randomSleep(timeRange: tuple = (5, 7)):
        '''随机暂停一段时间'''
        if len(timeRange) != 2:
            raise Exception("时间范围应包含开始与结束，列表长度应为2")
        a = timeRange[0]
        b = timeRange[1]
        sleepTime = random.uniform(a, b)
        LL.log(0, '程序正在暂停%.3f秒' % sleepTime)
        time.sleep(sleepTime)

    @staticmethod
    def genDeviceID(seed=time.time()):
        '''根据种子生成uuid'''
        with PseudoRandom(seed):
            def ranHex(x): return ''.join(
                random.choices('0123456789ABCDEF', k=x))  # 指定长度随机Hex字符串生成
            deviceId = "-".join([ranHex(8), ranHex(4), ranHex(4),
                                ranHex(4), ranHex(12)])  # 拼合字符串
        return deviceId


class DT:
    '''dict/list tools'''
    @staticmethod
    def loadYml(ymlDir='config.yml'):
        with open(ymlDir, 'r', encoding="utf-8") as f:
            return yaml.load(f, Loader=yaml.FullLoader)

    @staticmethod
    def writeYml(item, ymlDir='config.yml'):
        with open(ymlDir, 'w', encoding='utf-8') as f:
            yaml.dump(item, f, allow_unicode=True)

    @staticmethod
    def resJsonEncode(res):
        '''响应内容的json解析函数(换而言之，就是res.json()的小优化版本)'''
        try:
            return res.json()
        except Exception as e:
            raise Exception(
                f'响应内容以json格式解析失败({e})，响应内容:\n\n{res.text}')

    @staticmethod
    def formatStrList(item, returnSuperStr=False):
        '''字符串序列或字符串 格式化为 字符串列表。
        :feature: 超级字符串会被格式化为字符串
        :feature: 空值会被格式化为 空列表'''
        if isinstance(item, str):
            strList = [item]
        elif isinstance(item, dict):
            strList = [item]
        elif type(item) == SuperString:
            strList = [item]
        elif isinstance(item, Sequence):
            strList = list(item)
        elif not item:
            strList = []
        else:
            raise TypeError('请传入序列/字符串')
        # 格式化超级字符串
        for i, v in enumerate(strList):
            if isinstance(v, str) or isinstance(v, dict) or v == SuperString:
                if returnSuperStr:
                    strList[i] = SuperString(v)
                else:
                    strList[i] = str(SuperString(v))
        return strList

    @staticmethod
    def urlParamsToDict(url: str):
        '''提取url请求参数, 转为字典'''
        query = parse.urlparse(url).query
        params = parse.parse_qs(query)
        params = {k: v[0] for k, v in params.items()}
        return params


class CT:
    '''CryptoTools'''
    charset = 'utf-8'

    @staticmethod
    def pkcs7padding(text: str):
        """明文使用PKCS7填充"""
        remainder = 16 - len(text.encode(CT.charset)) % 16
        return str(text + chr(remainder) * remainder)

    @staticmethod
    def pkcs7unpadding(text: str):
        """去掉填充字符"""
        return text[:-ord(text[-1])]


class HSF:
    """Hashing String And File"""
    @staticmethod
    def geneHashObj(hash_type):
        if hash_type == 1:
            return hashlib.sha1()
        elif hash_type == 224:
            return hashlib.sha224()
        elif hash_type == 256:
            return hashlib.sha256()
        elif hash_type == 384:
            return hashlib.sha384()
        elif hash_type == 512:
            return hashlib.sha512()
        elif hash_type == 5:
            return hashlib.md5()
        elif hash_type == 3.224:
            return hashlib.sha3_224()
        elif hash_type == 3.256:
            return hashlib.sha3_256()
        elif hash_type == 3.384:
            return hashlib.sha3_384()
        elif hash_type == 3.512:
            return hashlib.sha3_512()
        else:
            raise Exception('类型错误, 初始化失败')

    @staticmethod
    def fileHash(path, hash_type):
        """计算文件哈希
        :param path: 文件路径
        :param hash_type: 哈希算法类型
            1       sha-1
            224     sha-224
            256      sha-256
            384     sha-384
            512     sha-512
            5       md5
            3.256   sha3-256
            3.384   sha3-384
            3.512   sha3-512
        """
        hashObj = HSF.geneHashObj(hash_type)
        if os.path.isfile(path):
            try:
                with open(path, "rb") as f:
                    for byte_block in iter(lambda: f.read(1048576), b""):
                        hashObj.update(byte_block)
                    return hashObj.hexdigest()
            except Exception as e:
                raise Exception('%s计算哈希出错: %s' % (path, e))
        else:
            raise Exception('路径错误, 没有指向文件: "%s"')

    @staticmethod
    def strHash(str_: str, hash_type, charset='utf-8'):
        """计算字符串哈希
        :param str_: 字符串
        :param hash_type: 哈希算法类型
        :param charset: 字符编码类型
            1       sha-1
            224     sha-224
            256      sha-256
            384     sha-384
            512     sha-512
            5       md5
            3.256   sha3-256
            3.384   sha3-384
            3.512   sha3-512
        """
        hashObj = HSF.geneHashObj(hash_type)
        bstr = str_.encode(charset)
        hashObj.update(bstr)
        return hashObj.hexdigest()


class ST:
    '''StringTools'''
    @staticmethod
    def timeFormating(string: str):
        '''字符串根据time.strftime()的规则，按照当前时间进行格式化'''
        return time.strftime(string, time.localtime())

    @staticmethod
    def randomFormating(string: str):
        r'''对字符串中的<rd>和</rd>之间(由\a分隔的字符串)随机选取一项加入到字符串中'''
        return re.sub(r"<rd>.*?</rd>", lambda x: random.choice(x.group()[4:-5].split('\a')), string)

    @staticmethod
    def avoidRegular(string: str):
        '''对字符串中的正则特殊符号前加上"\\", 并且在头尾加上"^"和"$"'''
        return '^' + re.sub(r"\.|\^|\$|\*|\+|\?|\{|\}|\[|\]|\(|\)|\||\\", lambda x: '\\'+x.group(), string) + "$"

    @staticmethod
    def notionStr(s: str):
        '''让输入的句子非常非常显眼'''
        return ('↓'*50 + '看这里' + '↓'*50 + '\n')*5 + s + ('\n' + '↑'*50 + '看这里' + '↑'*50)*5


class SuperString:
    '''超级字符串是带有flag的字符串。
    通过flag, 可以增加字符串功能(比如自动时间格式化/随机化), 定义匹配规则(正则/全等)'''

    def __init__(self, strLike):
        '''初始化超级字符串
        :param strLike: str|dict|SuperString
            : 字典要求{"str+": "字符串", "flag":"flag1|flag2"}形式'''
        # 参数初始化
        self.str = ''
        self.flags = []
        self.fStr = ''
        self.reFlag = False
        # 根据类型处理传入的项目
        if isinstance(strLike, str):
            self.str = str(strLike)
        elif isinstance(strLike, dict):
            if not ('str+' in strLike and 'flag' in strLike):
                raise TypeError('不支持缺少键"str+"或"flag"的字典转超级字符串')
            self.str = strLike['str+']
            self.flags = strLike['flag'].split('|')
        elif isinstance(strLike, SuperString):
            self.str = SuperString.str
            self.flags = SuperString.flags
        elif isinstance(strLike, (int, float, datetime.date, datetime.datetime)):
            self.str = str(strLike)
        else:
            raise TypeError(f'不支持[{type(strLike)}]转超级字符串')
        # 生成格式化字符串
        self.formating()
        # 判断self.match函数是否启用正则
        if 're' in self.flags:
            self.reFlag = True

    def formating(self):
        '''根据flags, 格式化字符串'''
        string = self.str
        for flag in self.flags:
            if flag == 'tf':
                string = ST.timeFormating(string)
            elif flag == 'rd':
                string = ST.randomFormating(string)
        self.fStr = string
        return self

    def match(self, str_):
        '''判断输入的字符串是否与超级字符串匹配'''
        if self.reFlag:
            return re.search(self.fStr, str_)
        else:
            return self.fStr == str_

    def __str__(self):
        return self.fStr


class ProxyGet:
    def __init__(self, config: dict):
        """
        params config: dict|str|none
        """
        self.config = config
        self.proxy = {}
        self.type = ""
        # 如果没有设置(代理)
        if not config:
            self.proxy = {}
            self.type = "none"
        # 如果是直接使用类型(代理)
        elif type(config) == str:
            if re.match(r"https?:\/\/", config):
                address = config
                self.proxy = {
                    'http': address,
                    'https': address
                }
                self.type = "normal"
            else:
                raise Exception("代理应以http://或https://为开头")
        # 如果是字典
        elif type(config) == dict:
            # 如果是直接使用类型(代理)
            if config.get("type") == "normal":
                self.proxy = config.get("address", {})
            # 如果是熊猫代理(http://www.xiongmaodaili.com/)的按量付费API
            elif config.get("type") == "panda":
                self.type = "panda"
                url = config["api"]
                # 解析url地址
                pa = parse.urlparse(url)
                self.api = pa.scheme + "://" + pa.netloc+pa.path
                # 解析url参数
                self.params = DT.urlParamsToDict(url)
                self.params.update({
                    "validTime": 1,
                    "isTxt": 0,
                    "count": 1
                })
                self.maxRetry = config.get("maxRetry", 1)
            else:
                Exception(f"不支持的配置[{config}]")
        else:
            raise TypeError(f"不支持[{type(config)}]类型的用户代理输入")

        # 检查直接使用的代理可用性
        if self.type == "normal" and NT.isDisableProxies(self.proxy):
            self.proxy = {}
            self.type = "normal"
            LL.log(2, f'[{self.proxy}]不可用, 已取消使用')

    def getProxy(self):
        if self.type == "normal" or self.type == "none":
            return self.proxy
        elif self.type == "panda":
            LL.log(0, "正在通过熊猫代理API获取代理")
            for times in range(1, self.maxRetry+1):
                try:
                    res = None
                    res = requests.get(self.api, params=self.params).json()
                    proxyLoc = res["obj"][0]
                    proxyUrl = f"http://{proxyLoc['ip']}:{proxyLoc['port']}"
                    proxy = {
                        'http': proxyUrl,
                        'https': proxyUrl
                    }
                    LL.log(1, f"通过熊猫代理API获取到代理[{proxy}]")
                    time.sleep(1)
                    if NT.isDisableProxies(proxy):
                        raise Exception(f"通过熊猫代理API获取到的代理不可用[{proxy}]")
                    LL.log(1, f"代理[{proxy}]可用")
                    return proxy
                except Exception as e:
                    msg = f"在尝试通过熊猫代理API获取代理时候发生错误\n可能的解决方案: \n1. 如果是云函数, 请确定开启固定出口IP功能, 否则无法使用熊猫代理。\n2. 刚开始使用时, 前几天的失败率较高\n错误: [{e}]\nres: [{res}]"
                    LL.log(2, msg)
                    if times == self.maxRetry:
                        LL.log(2, "取消使用代理")
                        return {}
                    else:
                        time.sleep(2)  # 熊猫代理最快一秒提取一次IP
                        pass


class SignTaskStatus:
    '''用于标记用户完成情况
    :code的含义
    0: 等待执行
    1: 出现错误(等待重试)
    100: 任务已被完成
    101: 该任务正常执行完成
    200: 用户设置不执行该任务
    201: 该任务不在执行时间
    300: 出错
    301: 当前情况无法完成该任务
    400: 没有找到需要执行的任务
    '''
    codeList = ('todo', 'done', 'skip', 'error', 'notFound')

    def __init__(self, code, msg=''):
        self.code = code
        self.msg = msg

    def codeHead(self):
        return int(self.code/100)

    def liteMsgEn(self):
        return SignTaskStatus.codeList[self.codeHead()]


class UserMsg:
    class Msg:
        def __init__(self):
            self.msgList = []

        def append(self, m: str):
            self.msgList.append(str(m))

        @property
        def text(self):
            return "\n".join(self.msgList)

    def __init__(self, users: list):
        """
        :params user: list[dict]: 用户列表
        """
        self.users = users

    @property
    def title_g1(self):
        """
        全局标题1
        示例: 『全局签到情况(114/514)[V-T1.0.0]』
        """
        codeCount = self.codeCount
        # generalSituations —— "done/(total-skip)"
        generalSituations = f'{codeCount[1]}/{sum(codeCount)-codeCount[2]}'
        return f"『全局签到情况({generalSituations})[{LL.prefix}]』"

    @property
    def time_g1(self):
        """
        时间统计1
        示例: Running at 2022-01-01 00:00:00, using 1145.14s
        """
        return f'Running at {TT.formatStartTime()}, using {TT.executionSeconds()}s'

    @property
    def count_g1(self):
        """
        签到情况统计1
        示例: 10: 1todo, 8done, 1skip, 0error, 0notFound
        """
        codeCount = self.codeCount
        cl = SignTaskStatus.codeList
        return f'{sum(codeCount)}: ' + ", ".join([f"{codeCount[i]}{cl[i]}" for i in range(len(cl))])

    @property
    def msg_g1(self):
        """
        全局签到情况1
        示例: 
        『全局签到情况(1/2)[V-T1.0.0]』
        [小李]
        --1145141919|1
        --初始化类失败，请键入完整的参数（用户名，密码，学校名称）
        [小王]
        --1145141919|1
        --[SUCCESS]须弥教令院每日打卡
        Running at 2022-01-01 00:00:00, using 1145.14s
        2: 0todo, 1done, 0skip, 1error, 0notFound
        """
        msg = UserMsg.Msg()
        msg.append(self.title_g1)
        for user in self.users:
            # 忽略跳过的任务
            if user['taskStatus'].codeHead() != 2:
                msg.append(f"[{user['remarkName']}]")
                msg.append(user['taskStatus'].msg)
        msg.append(self.time_g1)
        msg.append(self.count_g1)
        return msg.text

    @property
    def codeCount(self):
        """状态码统计"""
        codeList = [i['taskStatus'].codeHead() for i in self.users]
        codeCount = [0]*len(SignTaskStatus.codeList)
        for i in codeList:
            codeCount[i] += 1
        return codeCount
