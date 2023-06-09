
"""
EDCB に関連するユーティリティモジュール

The MIT License

Copyright (c) 2023 xtne6f

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import asyncio
import datetime
import socket
import time
from typing import Callable, Optional


class EDCBUtil:
    """ EDCB に関連する雑多なユーティリティ """

    @staticmethod
    def convertBytesToString(buf) -> str:
        """ BOM に基づいて Bytes データを文字列に変換する """
        if len(buf) == 0:
            return ''
        elif len(buf) >= 2 and buf[0] == 0xff and buf[1] == 0xfe:
            return str(memoryview(buf)[2:], 'utf_16_le', 'replace')
        elif len(buf) >= 3 and buf[0] == 0xef and buf[1] == 0xbb and buf[2] == 0xbf:
            return str(memoryview(buf)[3:], 'utf_8', 'replace')
        else:
            return str(buf, 'cp932', 'replace')

    @staticmethod
    def parseChSet5(s: str) -> list:
        """ ChSet5.txt を解析する """
        v = []
        for line in s.splitlines():
            a = line.split('\t')
            if len(a) >= 9:
                e: dict = {}
                try:
                    e['service_name'] = a[0]
                    e['network_name'] = a[1]
                    e['onid'] = int(a[2])
                    e['tsid'] = int(a[3])
                    e['sid'] = int(a[4])
                    e['service_type'] = int(a[5])
                    e['partial_flag'] = int(a[6]) != 0
                    e['epg_cap_flag'] = int(a[7]) != 0
                    e['search_flag'] = int(a[8]) != 0
                    v.append(e)
                except:
                    pass
        return v

    @staticmethod
    def getLogoIDFromLogoDataIni(s: str, onid: int, sid: int) -> int:
        """ LogoData.ini をもとにロゴ識別を取得する。失敗のとき負値を返す """
        target = f'{onid:04X}{sid:04X}'
        for line in s.splitlines():
            kv = line.split('=', 1)
            if len(kv) == 2 and kv[0].strip().upper() == target:
                try:
                    return int(kv[1].strip())
                except:
                    break
        return -1

    @staticmethod
    def getLogoFileNameFromDirectoryIndex(s: str, onid: int, logo_id: int, logo_type: int) -> Optional[str]:
        """ ファイルリストをもとにロゴファイル名を取得する """
        target = f'{onid:04X}_{logo_id:03X}_'
        target_type = f'_{logo_type:02d}.'
        for line in s.splitlines():
            a = line.split(' ', 3)
            if len(a) == 4:
                name = a[3]
                if len(name) >= 16 and name[0:9].upper() == target and name[12:16] == target_type:
                    return name
        return None

    @staticmethod
    def parseProgramExtendedText(s: str) -> dict:
        """ 詳細情報テキストを解析して項目ごとの辞書を返す """
        s = s.replace('\r', '')
        v = {}
        head = ''
        i = 0
        while True:
            if i == 0 and s.startswith('- '):
                j = 2
            elif (j := s.find('\n- ', i)) >= 0:
                v[head] = s[(0 if i == 0 else i + 1):j + 1]
                j += 3
            else:
                if len(s) != 0:
                    v[head] = s[(0 if i == 0 else i + 1):]
                break
            i = s.find('\n', j)
            if i < 0:
                v[s[j:]] = ''
                break
            head = s[j:i]
        return v

    @staticmethod
    async def openPipeStream(process_id: int, buffering: int, timeout_sec: float = 10.):
        """ システムに存在する SrvPipe ストリームを開き、ファイルオブジェクトを返す """
        to = time.monotonic() + timeout_sec
        wait = 0.1
        while time.monotonic() < to:
            # ポートは必ず 0 から 29 まで
            for port in range(30):
                try:
                    path = '\\\\.\\pipe\\SendTSTCP_' + str(port) + '_' + str(process_id)
                    return open(path, mode = 'rb', buffering = buffering)
                except:
                    pass
            await asyncio.sleep(wait)
            # 初期に成功しなければ見込みは薄いので問い合わせを疎にしていく
            wait = min(wait + 0.1, 1.0)
        return None

    @staticmethod
    async def openViewStream(host: str, port: int, process_id: int, timeout_sec: float = 10.) -> Optional[socket.socket]:
        """ View アプリの SrvPipe ストリームの転送を開始する """
        edcb = CtrlCmdUtil()
        edcb.setNWSetting(host, port)
        edcb.setConnectTimeOutSec(timeout_sec)
        to = time.monotonic() + timeout_sec
        wait = 0.1
        while time.monotonic() < to:
            sock = edcb.openViewStream(process_id)
            if sock is not None:
                return sock
            await asyncio.sleep(wait)
            # 初期に成功しなければ見込みは薄いので問い合わせを疎にしていく
            wait = min(wait + 0.1, 1.0)
        return None


class CtrlCmdUtil:
    """
    EpgTimerSrv の CtrlCmd インタフェースと通信する (EDCB/EpgTimer の CtrlCmd(Def).cs を移植したもの)
    ・利用可能なコマンドはもっとあるが使いそうなものだけ
    ・sendView* 系コマンドは EpgDataCap_Bon 等との通信用。接続先パイプは "View_Ctrl_BonNoWaitPipe_{プロセス ID}"
    """

    # EDCB の日付は OS のタイムゾーンに関わらず常に UTC+9
    TZ = datetime.timezone(datetime.timedelta(hours = 9), 'JST')

    def __init__(self) -> None:
        self.__connect_timeout_sec = 15.
        self.__pipe_name = 'EpgTimerSrvNoWaitPipe'
        self.__host: Optional[str] = None
        self.__port = 0

    def setPipeSetting(self, name: str) -> None:
        """ 名前付きパイプモードにする """
        self.__pipe_name = name
        self.__host = None

    def pipeExists(self) -> bool:
        """ 接続先パイプが存在するか調べる """
        try:
            with open('\\\\.\\pipe\\' + self.__pipe_name, mode = 'r+b'):
                pass
        except FileNotFoundError:
            return False
        except:
            pass
        return True

    def setNWSetting(self, host: str, port: int) -> None:
        """ TCP/IP モードにする """
        self.__host = host
        self.__port = port

    def setConnectTimeOutSec(self, timeout: float) -> None:
        """ 接続処理時のタイムアウト設定 """
        self.__connect_timeout_sec = timeout

    async def sendViewSetBonDriver(self, name: str) -> bool:
        """ BonDriver の切り替え """
        buf = bytearray()
        self.__writeInt(buf, self.__CMD_VIEW_APP_SET_BONDRIVER)
        self.__writeInt(buf, 0)
        self.__writeString(buf, name)
        self.__writeIntInplace(buf, 4, len(buf) - 8)
        ret, buf = await self.__sendAndReceive(buf)
        return ret == self.__CMD_SUCCESS

    async def sendViewGetBonDriver(self) -> Optional[str]:
        """ 使用中の BonDriver のファイル名を取得 """
        buf = bytearray()
        self.__writeInt(buf, self.__CMD_VIEW_APP_GET_BONDRIVER)
        self.__writeInt(buf, 0)
        ret, buf = await self.__sendAndReceive(buf)
        if ret == self.__CMD_SUCCESS:
            return self.__readString(memoryview(buf), [0], len(buf))
        return None

    async def sendViewSetCh(self, set_ch_info: dict) -> bool:
        """ チャンネル切り替え """
        buf = bytearray()
        self.__writeInt(buf, self.__CMD_VIEW_APP_SET_CH)
        self.__writeInt(buf, 0)
        self.__writeSetChInfo(buf, set_ch_info)
        self.__writeIntInplace(buf, 4, len(buf) - 8)
        ret, buf = await self.__sendAndReceive(buf)
        return ret == self.__CMD_SUCCESS

    async def sendViewAppClose(self) -> bool:
        """ アプリケーションの終了 """
        buf = bytearray()
        self.__writeInt(buf, self.__CMD_VIEW_APP_CLOSE)
        self.__writeInt(buf, 0)
        ret, buf = await self.__sendAndReceive(buf)
        return ret == self.__CMD_SUCCESS

    async def sendEnumService(self) -> Optional[list]:
        """ サービス一覧を取得する """
        buf = bytearray()
        self.__writeInt(buf, self.__CMD_EPG_SRV_ENUM_SERVICE)
        self.__writeInt(buf, 0)
        ret, buf = await self.__sendAndReceive(buf)
        if ret == self.__CMD_SUCCESS:
            return self.__readVector(self.__readServiceInfo, memoryview(buf), [0], len(buf))
        return None

    async def sendEnumPgInfoEx(self, service_time_list: list) -> Optional[list]:
        """ サービス指定と時間指定で番組情報一覧を取得する """
        buf = bytearray()
        self.__writeInt(buf, self.__CMD_EPG_SRV_ENUM_PG_INFO_EX)
        self.__writeInt(buf, 0)
        self.__writeVector(self.__writeLong, buf, service_time_list)
        self.__writeIntInplace(buf, 4, len(buf) - 8)
        ret, buf = await self.__sendAndReceive(buf)
        if ret == self.__CMD_SUCCESS:
            return self.__readVector(self.__readServiceEventInfo, memoryview(buf), [0], len(buf))
        return None

    async def sendEnumPgArc(self, service_time_list: list) -> Optional[list]:
        """ サービス指定と時間指定で過去番組情報一覧を取得する """
        buf = bytearray()
        self.__writeInt(buf, self.__CMD_EPG_SRV_ENUM_PG_ARC)
        self.__writeInt(buf, 0)
        self.__writeVector(self.__writeLong, buf, service_time_list)
        self.__writeIntInplace(buf, 4, len(buf) - 8)
        ret, buf = await self.__sendAndReceive(buf)
        if ret == self.__CMD_SUCCESS:
            return self.__readVector(self.__readServiceEventInfo, memoryview(buf), [0], len(buf))
        return None

    async def sendFileCopy(self, name: str) -> Optional[bytes]:
        """ 指定ファイルを転送する """
        buf = bytearray()
        self.__writeInt(buf, self.__CMD_EPG_SRV_FILE_COPY)
        self.__writeInt(buf, 0)
        self.__writeString(buf, name)
        self.__writeIntInplace(buf, 4, len(buf) - 8)
        ret, buf = await self.__sendAndReceive(buf)
        if ret == self.__CMD_SUCCESS:
            return buf
        return None

    async def sendFileCopy2(self, name_list: list) -> Optional[list]:
        """ 指定ファイルをまとめて転送する """
        buf = bytearray()
        self.__writeInt(buf, self.__CMD_EPG_SRV_FILE_COPY2)
        self.__writeInt(buf, 0)
        self.__writeUshort(buf, self.__CMD_VER)
        self.__writeVector(self.__writeString, buf, name_list)
        self.__writeIntInplace(buf, 4, len(buf) - 8)
        ret, buf = await self.__sendAndReceive(buf)
        if ret == self.__CMD_SUCCESS:
            bufview = memoryview(buf)
            pos = [0]
            if (ver := self.__readUshort(bufview, pos, len(buf))) is not None and ver >= self.__CMD_VER:
                return self.__readVector(self.__readFileData, bufview, pos, len(buf))
        return None

    async def sendNwTVIDSetCh(self, set_ch_info: dict) -> Optional[int]:
        """ NetworkTV モードの View アプリのチャンネルを切り替え、または起動の確認 (ID 指定) """
        buf = bytearray()
        self.__writeInt(buf, self.__CMD_EPG_SRV_NWTV_ID_SET_CH)
        self.__writeInt(buf, 0)
        self.__writeSetChInfo(buf, set_ch_info)
        self.__writeIntInplace(buf, 4, len(buf) - 8)
        ret, buf = await self.__sendAndReceive(buf)
        if ret == self.__CMD_SUCCESS:
            return self.__readInt(memoryview(buf), [0], len(buf))
        return None

    async def sendNwTVIDClose(self, nwtv_id: int) -> bool:
        """ NetworkTV モードで起動中の View アプリを終了 (ID 指定) """
        buf = bytearray()
        self.__writeInt(buf, self.__CMD_EPG_SRV_NWTV_ID_CLOSE)
        self.__writeInt(buf, 0)
        self.__writeInt(buf, nwtv_id)
        self.__writeIntInplace(buf, 4, len(buf) - 8)
        ret, buf = await self.__sendAndReceive(buf)
        return ret == self.__CMD_SUCCESS

    async def sendEnumRecInfoBasic2(self) -> Optional[list]:
        """ 録画済み情報一覧取得 (programInfo と errInfo を除く) """
        buf = bytearray()
        self.__writeInt(buf, self.__CMD_EPG_SRV_ENUM_RECINFO_BASIC2)
        self.__writeInt(buf, 0)
        self.__writeUshort(buf, self.__CMD_VER)
        self.__writeIntInplace(buf, 4, len(buf) - 8)
        ret, buf = await self.__sendAndReceive(buf)
        if ret == self.__CMD_SUCCESS:
            bufview = memoryview(buf)
            pos = [0]
            if (ver := self.__readUshort(bufview, pos, len(buf))) is not None and ver >= self.__CMD_VER:
                return self.__readVector(self.__readRecFileInfo, bufview, pos, len(buf))
        return None

    async def sendGetRecInfo2(self, info_id: int) -> Optional[dict]:
        """ 録画済み情報取得 """
        buf = bytearray()
        self.__writeInt(buf, self.__CMD_EPG_SRV_GET_RECINFO2)
        self.__writeInt(buf, 0)
        self.__writeUshort(buf, self.__CMD_VER)
        self.__writeInt(buf, info_id)
        self.__writeIntInplace(buf, 4, len(buf) - 8)
        ret, buf = await self.__sendAndReceive(buf)
        if ret == self.__CMD_SUCCESS:
            bufview = memoryview(buf)
            pos = [0]
            if (ver := self.__readUshort(bufview, pos, len(buf))) is not None and ver >= self.__CMD_VER:
                return self.__readRecFileInfo(bufview, pos, len(buf))
        return None

    def openViewStream(self, process_id: int) -> Optional[socket.socket]:
        """ View アプリの SrvPipe ストリームの転送を開始する """
        buf = bytearray()
        self.__writeInt(buf, self.__CMD_EPG_SRV_RELAY_VIEW_STREAM)
        self.__writeInt(buf, 4)
        self.__writeInt(buf, process_id)

        # TCP/IP モードであること
        if self.__host is None:
            return None

        try:
            sock = socket.create_connection((self.__host, self.__port), self.__connect_timeout_sec)
        except:
            return None
        try:
            sock.settimeout(self.__connect_timeout_sec)
            sock.sendall(buf)
            rbuf = bytearray()
            while len(rbuf) < 8:
                r = sock.recv(8 - len(rbuf))
                if not r:
                    break
                rbuf.extend(r)
        except:
            sock.close()
            return None

        if len(rbuf) == 8:
            ret = self.__readInt(memoryview(rbuf), [0], 8)
            if ret == self.__CMD_SUCCESS:
                return sock
        sock.close()
        return None

    # EDCB/EpgTimer の CtrlCmd.cs より
    __CMD_SUCCESS = 1
    __CMD_VER = 5
    __CMD_VIEW_APP_SET_BONDRIVER = 201
    __CMD_VIEW_APP_GET_BONDRIVER = 202
    __CMD_VIEW_APP_SET_CH = 205
    __CMD_VIEW_APP_CLOSE = 208
    __CMD_EPG_SRV_RELAY_VIEW_STREAM = 301
    __CMD_EPG_SRV_ENUM_SERVICE = 1021
    __CMD_EPG_SRV_ENUM_PG_INFO_EX = 1029
    __CMD_EPG_SRV_ENUM_PG_ARC = 1030
    __CMD_EPG_SRV_FILE_COPY = 1060
    __CMD_EPG_SRV_NWTV_ID_SET_CH = 1073
    __CMD_EPG_SRV_NWTV_ID_CLOSE = 1074
    __CMD_EPG_SRV_ENUM_RECINFO_BASIC2 = 2020
    __CMD_EPG_SRV_GET_RECINFO2 = 2024
    __CMD_EPG_SRV_FILE_COPY2 = 2060

    async def __sendAndReceive(self, buf: bytearray):
        to = time.monotonic() + self.__connect_timeout_sec
        if self.__host is None:
            # 名前付きパイプモード
            while True:
                try:
                    with open('\\\\.\\pipe\\' + self.__pipe_name, mode = 'r+b') as f:
                        f.write(buf)
                        f.flush()
                        rbuf = f.read(8)
                        if len(rbuf) == 8:
                            bufview = memoryview(rbuf)
                            pos = [0]
                            ret = self.__readInt(bufview, pos, 8)
                            size = self.__readInt(bufview, pos, 8)
                            rbuf = f.read(size)
                            if len(rbuf) == size:
                                return ret, rbuf
                    break
                except FileNotFoundError:
                    break
                except:
                    pass
                await asyncio.sleep(0.01)
                if time.monotonic() >= to:
                    break
            return None, None

        # TCP/IP モード
        try:
            r, w = await asyncio.wait_for(asyncio.open_connection(self.__host, self.__port), max(to - time.monotonic(), 0.))
        except:
            return None, None
        try:
            w.write(buf)
            await asyncio.wait_for(w.drain(), max(to - time.monotonic(), 0.))
            rbuf = await asyncio.wait_for(r.readexactly(8), max(to - time.monotonic(), 0.))
            if len(rbuf) == 8:
                bufview = memoryview(rbuf)
                pos = [0]
                ret = self.__readInt(bufview, pos, 8)
                size = self.__readInt(bufview, pos, 8)
                rbuf = await asyncio.wait_for(r.readexactly(size), max(to - time.monotonic(), 0.))
        except:
            w.close()
            return None, None
        try:
            w.close()
            await asyncio.wait_for(w.wait_closed(), max(to - time.monotonic(), 0.))
        except:
            pass
        if len(rbuf) == size:
            return ret, rbuf
        return None, None

    @staticmethod
    def __writeByte(buf: bytearray, v: int) -> None:
        buf.extend(v.to_bytes(1, 'little'))

    @staticmethod
    def __writeUshort(buf: bytearray, v: int) -> None:
        buf.extend(v.to_bytes(2, 'little'))

    @staticmethod
    def __writeInt(buf: bytearray, v: int) -> None:
        buf.extend(v.to_bytes(4, 'little', signed = True))

    @staticmethod
    def __writeLong(buf: bytearray, v: int) -> None:
        buf.extend(v.to_bytes(8, 'little', signed = True))

    @staticmethod
    def __writeIntInplace(buf: bytearray, pos: int, v: int) -> None:
        buf[pos:pos + 4] = v.to_bytes(4, 'little', signed = True)

    @classmethod
    def __writeString(cls, buf: bytearray, v: str) -> None:
        vv = v.encode('utf_16_le')
        cls.__writeInt(buf, 6 + len(vv))
        buf.extend(vv)
        cls.__writeUshort(buf, 0)

    @classmethod
    def __writeVector(cls, write_func: Callable, buf: bytearray, v: list) -> None:
        pos = len(buf)
        cls.__writeInt(buf, 0)
        cls.__writeInt(buf, len(v))
        for e in v:
            write_func(buf, e)
        cls.__writeIntInplace(buf, pos, len(buf) - pos)

    # 以下、各構造体のライター
    # 各キーの意味は CtrlCmdDef.cs のクラス定義を参照のこと

    @classmethod
    def __writeSetChInfo(cls, buf: bytearray, v: dict) -> None:
        pos = len(buf)
        cls.__writeInt(buf, 0)
        cls.__writeInt(buf, 1 if v.get('use_sid') else 0)
        cls.__writeUshort(buf, v.get('onid', 0))
        cls.__writeUshort(buf, v.get('tsid', 0))
        cls.__writeUshort(buf, v.get('sid', 0))
        cls.__writeInt(buf, 1 if v.get('use_bon_ch') else 0)
        cls.__writeInt(buf, v.get('space_or_id', 0))
        cls.__writeInt(buf, v.get('ch_or_mode', 0))
        cls.__writeIntInplace(buf, pos, len(buf) - pos)

    @staticmethod
    def __readByte(buf: memoryview, pos: list, size: int, dest: Optional[dict] = None, key: Optional[str] = None):
        if size - pos[0] < 1:
            return None if dest is None else False
        v = int.from_bytes(buf[pos[0]:pos[0] + 1], 'little')
        pos[0] += 1
        if dest is None:
            return v
        dest[key] = v
        return True

    @staticmethod
    def __readUshort(buf: memoryview, pos: list, size: int, dest: Optional[dict] = None, key: Optional[str] = None):
        if size - pos[0] < 2:
            return None if dest is None else False
        v = int.from_bytes(buf[pos[0]:pos[0] + 2], 'little')
        pos[0] += 2
        if dest is None:
            return v
        dest[key] = v
        return True

    @staticmethod
    def __readInt(buf: memoryview, pos: list, size: int, dest: Optional[dict] = None, key: Optional[str] = None):
        if size - pos[0] < 4:
            return None if dest is None else False
        v = int.from_bytes(buf[pos[0]:pos[0] + 4], 'little', signed = True)
        pos[0] += 4
        if dest is None:
            return v
        dest[key] = v
        return True

    @staticmethod
    def __readLong(buf: memoryview, pos: list, size: int, dest: Optional[dict] = None, key: Optional[str] = None):
        if size - pos[0] < 8:
            return None if dest is None else False
        v = int.from_bytes(buf[pos[0]:pos[0] + 8], 'little', signed = True)
        pos[0] += 8
        if dest is None:
            return v
        dest[key] = v
        return True

    @classmethod
    def __readSystemTime(cls, buf: memoryview, pos: list, size: int, dest: Optional[dict] = None, key: Optional[str] = None):
        if size - pos[0] < 16:
            return None if dest is None else False
        try:
            v = datetime.datetime(int.from_bytes(buf[pos[0]:pos[0] + 2], 'little'),
                                  int.from_bytes(buf[pos[0] + 2:pos[0] + 4], 'little'),
                                  int.from_bytes(buf[pos[0] + 6:pos[0] + 8], 'little'),
                                  int.from_bytes(buf[pos[0] + 8:pos[0] + 10], 'little'),
                                  int.from_bytes(buf[pos[0] + 10:pos[0] + 12], 'little'),
                                  int.from_bytes(buf[pos[0] + 12:pos[0] + 14], 'little'),
                                  tzinfo = cls.TZ)
        except:
            v = datetime.datetime.min
        pos[0] += 16
        if dest is None:
            return v
        dest[key] = v
        return True

    @classmethod
    def __readString(cls, buf: memoryview, pos: list, size: int, dest: Optional[dict] = None, key: Optional[str] = None):
        vs = cls.__readInt(buf, pos, size)
        if vs is None or vs < 6 or size - pos[0] < vs - 4:
            return None if dest is None else False
        v = str(buf[pos[0]:pos[0] + vs - 6], 'utf_16_le')
        pos[0] += vs - 4
        if dest is None:
            return v
        dest[key] = v
        return True

    @classmethod
    def __readVector(cls, read_func: Callable, buf: memoryview, pos: list, size: int, dest: Optional[dict] = None, key: Optional[str] = None):
        if ((vs := cls.__readInt(buf, pos, size)) is None or
            (vc := cls.__readInt(buf, pos, size)) is None or
            vs < 8 or vc < 0 or size - pos[0] < vs - 8):
            return None if dest is None else False
        size = pos[0] + vs - 8
        v = []
        for i in range(vc):
            e = read_func(buf, pos, size)
            if e is None:
                return None if dest is None else False
            v.append(e)
        pos[0] = size
        if dest is None:
            return v
        dest[key] = v
        return True

    @classmethod
    def __readStructIntro(cls, buf: memoryview, pos: list, size: int):
        vs = cls.__readInt(buf, pos, size)
        if vs is None or vs < 4 or size - pos[0] < vs - 4:
            return None, 0
        return {}, pos[0] + vs - 4

    # 以下、各構造体のリーダー
    # 各キーの意味は CtrlCmdDef.cs のクラス定義を参照のこと

    @classmethod
    def __readFileData(cls, buf: memoryview, pos: list, size: int) -> Optional[dict]:
        v, size = cls.__readStructIntro(buf, pos, size)
        if (v is None or
            not cls.__readString(buf, pos, size, v, 'name') or
            (data_size := cls.__readInt(buf, pos, size)) is None or
            cls.__readInt(buf, pos, size) is None or
            data_size < 0 or size - pos[0] < data_size):
            return None
        v['data'] = bytes(buf[pos[0]:pos[0] + data_size])
        pos[0] = size
        return v

    @classmethod
    def __readRecFileInfo(cls, buf: memoryview, pos: list, size: int) -> Optional[dict]:
        v, size = cls.__readStructIntro(buf, pos, size)
        if (v is None or
            not cls.__readInt(buf, pos, size, v, 'id') or
            not cls.__readString(buf, pos, size, v, 'rec_file_path') or
            not cls.__readString(buf, pos, size, v, 'title') or
            not cls.__readSystemTime(buf, pos, size, v, 'start_time') or
            not cls.__readInt(buf, pos, size, v, 'duration_sec') or
            not cls.__readString(buf, pos, size, v, 'service_name') or
            not cls.__readUshort(buf, pos, size, v, 'onid') or
            not cls.__readUshort(buf, pos, size, v, 'tsid') or
            not cls.__readUshort(buf, pos, size, v, 'sid') or
            not cls.__readUshort(buf, pos, size, v, 'eid') or
            not cls.__readLong(buf, pos, size, v, 'drops') or
            not cls.__readLong(buf, pos, size, v, 'scrambles') or
            not cls.__readInt(buf, pos, size, v, 'rec_status') or
            not cls.__readSystemTime(buf, pos, size, v, 'start_time_epg') or
            not cls.__readString(buf, pos, size, v, 'comment') or
            not cls.__readString(buf, pos, size, v, 'program_info') or
            not cls.__readString(buf, pos, size, v, 'err_info') or
            not cls.__readByte(buf, pos, size, v, 'protect_flag')):
            return None
        pos[0] = size
        return v

    @classmethod
    def __readServiceEventInfo(cls, buf: memoryview, pos: list, size: int) -> Optional[dict]:
        v, size = cls.__readStructIntro(buf, pos, size)
        if v is None:
            return None
        v['service_info'] = cls.__readServiceInfo(buf, pos, size)
        if (v['service_info'] is None or
            not cls.__readVector(cls.__readEventInfo, buf, pos, size, v, 'event_list')):
            return None
        pos[0] = size
        return v

    @classmethod
    def __readServiceInfo(cls, buf: memoryview, pos: list, size: int) -> Optional[dict]:
        v, size = cls.__readStructIntro(buf, pos, size)
        if (v is None or
            not cls.__readUshort(buf, pos, size, v, 'onid') or
            not cls.__readUshort(buf, pos, size, v, 'tsid') or
            not cls.__readUshort(buf, pos, size, v, 'sid') or
            not cls.__readByte(buf, pos, size, v, 'service_type') or
            not cls.__readByte(buf, pos, size, v, 'partial_reception_flag') or
            not cls.__readString(buf, pos, size, v, 'service_provider_name') or
            not cls.__readString(buf, pos, size, v, 'service_name') or
            not cls.__readString(buf, pos, size, v, 'network_name') or
            not cls.__readString(buf, pos, size, v, 'ts_name') or
            not cls.__readByte(buf, pos, size, v, 'remote_control_key_id')):
            return None
        pos[0] = size
        return v

    @classmethod
    def __readEventInfo(cls, buf: memoryview, pos: list, size: int) -> Optional[dict]:
        v, size = cls.__readStructIntro(buf, pos, size)
        if (v is None or
            not cls.__readUshort(buf, pos, size, v, 'onid') or
            not cls.__readUshort(buf, pos, size, v, 'tsid') or
            not cls.__readUshort(buf, pos, size, v, 'sid') or
            not cls.__readUshort(buf, pos, size, v, 'eid') or
            (start_time_flag := cls.__readByte(buf, pos, size)) is None or
            not cls.__readSystemTime(buf, pos, size, v, 'start_time') or
            (duration_flag := cls.__readByte(buf, pos, size)) is None or
            not cls.__readInt(buf, pos, size, v, 'duration_sec')):
            return None

        if start_time_flag == 0:
            del v['start_time']
        if duration_flag == 0:
            del v['duration_sec']

        if (n := cls.__readInt(buf, pos, size)) is None:
            return None
        if n != 4:
            pos[0] -= 4
            v['short_info'] = cls.__readShortEventInfo(buf, pos, size);
            if v['short_info'] is None:
                return None

        if (n := cls.__readInt(buf, pos, size)) is None:
            return None
        if n != 4:
            pos[0] -= 4
            v['ext_info'] = cls.__readExtendedEventInfo(buf, pos, size);
            if v['ext_info'] is None:
                return None

        if (n := cls.__readInt(buf, pos, size)) is None:
            return None
        if n != 4:
            pos[0] -= 4
            v['content_info'] = cls.__readContentInfo(buf, pos, size);
            if v['content_info'] is None:
                return None

        if (n := cls.__readInt(buf, pos, size)) is None:
            return None
        if n != 4:
            pos[0] -= 4
            v['component_info'] = cls.__readComponentInfo(buf, pos, size);
            if v['component_info'] is None:
                return None

        if (n := cls.__readInt(buf, pos, size)) is None:
            return None
        if n != 4:
            pos[0] -= 4
            v['audio_info'] = cls.__readAudioComponentInfo(buf, pos, size);
            if v['audio_info'] is None:
                return None

        if (n := cls.__readInt(buf, pos, size)) is None:
            return None
        if n != 4:
            pos[0] -= 4
            v['event_group_info'] = cls.__readEventGroupInfo(buf, pos, size);
            if v['event_group_info'] is None:
                return None

        if (n := cls.__readInt(buf, pos, size)) is None:
            return None
        if n != 4:
            pos[0] -= 4
            v['event_relay_info'] = cls.__readEventGroupInfo(buf, pos, size);
            if v['event_relay_info'] is None:
                return None

        if not cls.__readByte(buf, pos, size, v, 'free_ca_flag'):
            return None
        pos[0] = size
        return v

    @classmethod
    def __readShortEventInfo(cls, buf: memoryview, pos: list, size: int) -> Optional[dict]:
        v, size = cls.__readStructIntro(buf, pos, size)
        if (v is None or
            not cls.__readString(buf, pos, size, v, 'event_name') or
            not cls.__readString(buf, pos, size, v, 'text_char')):
            return None
        pos[0] = size
        return v

    @classmethod
    def __readExtendedEventInfo(cls, buf: memoryview, pos: list, size: int) -> Optional[dict]:
        v, size = cls.__readStructIntro(buf, pos, size)
        if (v is None or
            not cls.__readString(buf, pos, size, v, 'text_char')):
            return None
        pos[0] = size
        return v

    @classmethod
    def __readContentInfo(cls, buf: memoryview, pos: list, size: int) -> Optional[dict]:
        v, size = cls.__readStructIntro(buf, pos, size)
        if (v is None or
            not cls.__readVector(cls.__readContentData, buf, pos, size, v, 'nibble_list')):
            return None
        pos[0] = size
        return v

    @classmethod
    def __readContentData(cls, buf: memoryview, pos: list, size: int) -> Optional[dict]:
        v, size = cls.__readStructIntro(buf, pos, size)
        if (v is None or
            (cn := cls.__readUshort(buf, pos, size)) is None or
            (un := cls.__readUshort(buf, pos, size)) is None):
            return None
        v['content_nibble'] = (cn >> 8 | cn << 8) & 0xffff
        v['user_nibble'] = (un >> 8 | un << 8) & 0xffff
        pos[0] = size
        return v

    @classmethod
    def __readComponentInfo(cls, buf: memoryview, pos: list, size: int) -> Optional[dict]:
        v, size = cls.__readStructIntro(buf, pos, size)
        if (v is None or
            not cls.__readByte(buf, pos, size, v, 'stream_content') or
            not cls.__readByte(buf, pos, size, v, 'component_type') or
            not cls.__readByte(buf, pos, size, v, 'component_tag') or
            not cls.__readString(buf, pos, size, v, 'text_char')):
            return None
        pos[0] = size
        return v

    @classmethod
    def __readAudioComponentInfo(cls, buf: memoryview, pos: list, size: int) -> Optional[dict]:
        v, size = cls.__readStructIntro(buf, pos, size)
        if (v is None or
            not cls.__readVector(cls.__readAudioComponentInfoData, buf, pos, size, v, 'component_list')):
            return None
        pos[0] = size
        return v

    @classmethod
    def __readAudioComponentInfoData(cls, buf: memoryview, pos: list, size: int) -> Optional[dict]:
        v, size = cls.__readStructIntro(buf, pos, size)
        if (v is None or
            not cls.__readByte(buf, pos, size, v, 'stream_content') or
            not cls.__readByte(buf, pos, size, v, 'component_type') or
            not cls.__readByte(buf, pos, size, v, 'component_tag') or
            not cls.__readByte(buf, pos, size, v, 'stream_type') or
            not cls.__readByte(buf, pos, size, v, 'simulcast_group_tag') or
            not cls.__readByte(buf, pos, size, v, 'es_multi_lingual_flag') or
            not cls.__readByte(buf, pos, size, v, 'main_component_flag') or
            not cls.__readByte(buf, pos, size, v, 'quality_indicator') or
            not cls.__readByte(buf, pos, size, v, 'sampling_rate') or
            not cls.__readString(buf, pos, size, v, 'text_char')):
            return None
        pos[0] = size
        return v

    @classmethod
    def __readEventGroupInfo(cls, buf: memoryview, pos: list, size: int) -> Optional[dict]:
        v, size = cls.__readStructIntro(buf, pos, size)
        if (v is None or
            not cls.__readByte(buf, pos, size, v, 'group_type') or
            not cls.__readVector(cls.__readEventData, buf, pos, size, v, 'event_data_list')):
            return None
        pos[0] = size
        return v

    @classmethod
    def __readEventData(cls, buf: memoryview, pos: list, size: int) -> Optional[dict]:
        v, size = cls.__readStructIntro(buf, pos, size)
        if (v is None or
            not cls.__readUshort(buf, pos, size, v, 'onid') or
            not cls.__readUshort(buf, pos, size, v, 'tsid') or
            not cls.__readUshort(buf, pos, size, v, 'sid') or
            not cls.__readUshort(buf, pos, size, v, 'eid')):
            return None
        pos[0] = size
        return v
