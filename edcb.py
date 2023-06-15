
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
from typing import BinaryIO, Callable, Literal, TypedDict, TypeVar

# ジェネリック型
T = TypeVar('T')


class ChSet5Item(TypedDict):
    """ ChSet5.txt の一行の情報 """
    service_name: str
    network_name: str
    onid: int
    tsid: int
    sid: int
    service_type: int
    partial_flag: bool
    epg_cap_flag: bool
    search_flag: bool


class EDCBUtil:
    """ EDCB に関連する雑多なユーティリティ """

    @staticmethod
    def convertBytesToString(buf: bytes | bytearray | memoryview) -> str:
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
    def parseChSet5(s: str) -> list[ChSet5Item]:
        """ ChSet5.txt を解析する """
        v: list[ChSet5Item] = []
        for line in s.splitlines():
            a = line.split('\t')
            if len(a) >= 9:
                try:
                    v.append({
                        'service_name': a[0],
                        'network_name': a[1],
                        'onid': int(a[2]),
                        'tsid': int(a[3]),
                        'sid': int(a[4]),
                        'service_type': int(a[5]),
                        'partial_flag': int(a[6]) != 0,
                        'epg_cap_flag': int(a[7]) != 0,
                        'search_flag': int(a[8]) != 0
                    })
                except Exception:
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
                except Exception:
                    break
        return -1

    @staticmethod
    def getLogoFileNameFromDirectoryIndex(s: str, onid: int, logo_id: int, logo_type: int) -> str | None:
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
    def parseProgramExtendedText(s: str) -> dict[str, str]:
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
    def datetimeToFileTime(dt: datetime.datetime, tz: datetime.timezone = datetime.UTC) -> int:
        """ FILETIME 時間 (1601 年からの 100 ナノ秒時刻) に変換する """
        return int((dt.timestamp() + tz.utcoffset(None).total_seconds()) * 10000000) + 116444736000000000

    @staticmethod
    async def openPipeStream(process_id: int, buffering: int, timeout_sec: float = 10.) -> BinaryIO | None:
        """ システムに存在する SrvPipe ストリームを開き、ファイルオブジェクトを返す """
        to = time.monotonic() + timeout_sec
        wait = 0.1
        while time.monotonic() < to:
            # ポートは必ず 0 から 29 まで
            for port in range(30):
                try:
                    path = '\\\\.\\pipe\\SendTSTCP_' + str(port) + '_' + str(process_id)
                    return open(path, mode='rb', buffering=buffering)
                except Exception:
                    pass
            await asyncio.sleep(wait)
            # 初期に成功しなければ見込みは薄いので問い合わせを疎にしていく
            wait = min(wait + 0.1, 1.0)
        return None

    @staticmethod
    async def openViewStream(host: str, port: int, process_id: int, timeout_sec: float = 10.) -> socket.socket | None:
        """
        View アプリの SrvPipe ストリームの転送を開始する

        ストリーム接続処理や返却するソケットは非同期のものでないことに注意し、長めの I/O 待ちが許さ
        れないアプリケーションでは非同期ソケットへの改変またはワーカースレッドの利用を検討すること。
        """
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


# 以下、 CtrlCmdUtil で受け渡しする辞書の型ヒント
# ・キーの意味は https://github.com/xtne6f/EDCB の Readme_Mod.txt のテーブル定義の対応する説明を参照
#   のこと。キーについてのコメントはこの説明と異なるものだけ行う
# ・辞書やキーの命名は EpgTimer の CtrlCmdDef.cs を基準とする
# ・注記がなければ受け取り方向ではすべてのキーが存在し、引き渡し方向は存在しないキーを 0 や False や
#   空文字列などとして解釈する


class SetChInfo(TypedDict, total=False):
    """ チャンネル・ NetworkTV モード変更情報 """
    use_sid: int
    onid: int
    tsid: int
    sid: int
    use_bon_ch: int
    space_or_id: int
    ch_or_mode: int


class ServiceInfo(TypedDict):
    """ サービス情報 """
    onid: int
    tsid: int
    sid: int
    service_type: int
    partial_reception_flag: int
    service_provider_name: str
    service_name: str
    network_name: str
    ts_name: str
    remote_control_key_id: int


class FileData(TypedDict):
    """ 転送ファイルデータ """
    name: str
    data: bytes


class RecFileSetInfo(TypedDict, total=False):
    """ 録画フォルダ情報 """
    rec_folder: str
    write_plug_in: str
    rec_name_plug_in: str


class RecSettingData(TypedDict, total=False):
    """ 録画設定 """
    rec_mode: int  # 0-4: 全サービス～視聴, 5-8: 無効の指定サービス～視聴, 9: 無効の全サービス
    priority: int
    tuijyuu_flag: bool
    service_mode: int
    pittari_flag: bool
    bat_file_path: str
    rec_folder_list: list[RecFileSetInfo]
    suspend_mode: int
    reboot_flag: bool
    start_margin: int  # デフォルトのとき存在しない
    end_margin: int  # デフォルトのとき存在しない
    continue_rec_flag: bool
    partial_rec_flag: int
    tuner_id: int
    partial_rec_folder: list[RecFileSetInfo]


class ReserveData(TypedDict, total=False):
    """ 予約情報 """
    title: str
    start_time: datetime.datetime
    duration_second: int
    station_name: str
    onid: int
    tsid: int
    sid: int
    eid: int
    comment: str
    reserve_id: int
    overlap_mode: int
    start_time_epg: datetime.datetime
    rec_setting: RecSettingData
    rec_file_name_list: list[str]  # 録画予定ファイル名


class RecFileInfo(TypedDict, total=False):
    """ 録画済み情報 """
    id: int
    rec_file_path: str
    title: str
    start_time: datetime.datetime
    duration_sec: int
    service_name: str
    onid: int
    tsid: int
    sid: int
    eid: int
    drops: int
    scrambles: int
    rec_status: int
    start_time_epg: datetime.datetime
    comment: str
    program_info: str
    err_info: str
    protect_flag: bool


class TunerReserveInfo(TypedDict):
    """ チューナー予約情報 """
    tuner_id: int
    tuner_name: str
    reserve_list: list[int]


class ShortEventInfo(TypedDict):
    """ イベントの基本情報 """
    event_name: str
    text_char: str


class ExtendedEventInfo(TypedDict):
    """ イベントの拡張情報 """
    text_char: str


class ContentData(TypedDict):
    """ ジャンルの個別データ """
    content_nibble: int
    user_nibble: int


class ContentInfo(TypedDict):
    """ ジャンル情報 """
    nibble_list: list[ContentData]


class ComponentInfo(TypedDict):
    """ 映像情報 """
    stream_content: int
    component_type: int
    component_tag: int
    text_char: str


class AudioComponentInfoData(TypedDict):
    """ 音声情報の個別データ """
    stream_content: int
    component_type: int
    component_tag: int
    stream_type: int
    simulcast_group_tag: int
    es_multi_lingual_flag: int
    main_component_flag: int
    quality_indicator: int
    sampling_rate: int
    text_char: str


class AudioComponentInfo(TypedDict):
    """ 音声情報 """
    component_list: list[AudioComponentInfoData]


class EventData(TypedDict):
    """ イベントグループの個別データ """
    onid: int
    tsid: int
    sid: int
    eid: int


class EventGroupInfo(TypedDict):
    """ イベントグループ情報 """
    group_type: int
    event_data_list: list[EventData]


class EventInfoRequired(TypedDict):
    """ イベント情報の必須項目 """
    onid: int
    tsid: int
    sid: int
    eid: int
    free_ca_flag: int


class EventInfo(EventInfoRequired, total=False):
    """ イベント情報 """
    start_time: datetime.datetime  # 不明のとき存在しない
    duration_sec: int  # 不明のとき存在しない
    short_info: ShortEventInfo  # 情報がないとき存在しない、以下同様
    ext_info: ExtendedEventInfo
    content_info: ContentInfo
    component_info: ComponentInfo
    audio_info: AudioComponentInfo
    event_group_info: EventGroupInfo
    event_relay_info: EventGroupInfo


class ServiceEventInfo(TypedDict):
    """ サービスとそのイベント一覧 """
    service_info: ServiceInfo
    event_list: list[EventInfo]


class SearchDateInfo(TypedDict, total=False):
    """ 対象期間 """
    start_day_of_week: int
    start_hour: int
    start_min: int
    end_day_of_week: int
    end_hour: int
    end_min: int


class SearchKeyInfo(TypedDict, total=False):
    """ 検索条件 """
    and_key: str  # 登録無効、大小文字区別、番組長についての接頭辞は処理済み
    not_key: str
    key_disabled: bool
    case_sensitive: bool
    reg_exp_flag: bool
    title_only_flag: bool
    content_list: list[ContentData]
    date_list: list[SearchDateInfo]
    service_list: list[int]  # (onid << 32 | tsid << 16 | sid) のリスト
    video_list: list[int]  # 無視してよい
    audio_list: list[int]  # 無視してよい
    aimai_flag: bool
    not_contet_flag: bool
    not_date_flag: bool
    free_ca_flag: int
    chk_rec_end: bool
    chk_rec_day: int
    chk_rec_no_service: bool
    chk_duration_min: int
    chk_duration_max: int


class AutoAddData(TypedDict, total=False):
    """ 自動予約登録情報 """
    data_id: int
    search_info: SearchKeyInfo
    rec_setting: RecSettingData
    add_count: int


class ManualAutoAddData(TypedDict, total=False):
    """ 自動予約 (プログラム) 登録情報 """
    data_id: int
    day_of_week_flag: int
    start_time: int
    duration_second: int
    title: str
    station_name: str
    onid: int
    tsid: int
    sid: int
    rec_setting: RecSettingData


class NotifySrvInfo(TypedDict):
    """ 情報通知用パラメーター """
    notify_id: int  # 通知情報の種類
    time: datetime.datetime  # 通知状態の発生した時間
    param1: int  # パラメーター1 (種類によって内容変更)
    param2: int  # パラメーター2 (種類によって内容変更)
    count: int  # 通知の巡回カウンタ
    param4: str  # パラメーター4 (種類によって内容変更)
    param5: str  # パラメーター5 (種類によって内容変更)
    param6: str  # パラメーター6 (種類によって内容変更)


# 以上、 CtrlCmdUtil で受け渡しする辞書の型ヒント


class NotifyUpdate:
    """ 通知情報の種類 """
    EPGDATA = 1  # EPGデータが更新された
    RESERVE_INFO = 2  # 予約情報が更新された
    REC_INFO = 3  # 録画済み情報が更新された
    AUTOADD_EPG = 4  # 自動予約登録情報が更新された
    AUTOADD_MANUAL = 5  # 自動予約 (プログラム) 登録情報が更新された
    PROFILE = 51  # 設定ファイル (ini) が更新された
    SRV_STATUS = 100  # Srv の動作状況が変更 (param1: ステータス 0:通常、1:録画中、2:EPG取得中)
    PRE_REC_START = 101  # 録画準備開始 (param4: ログ用メッセージ)
    REC_START = 102  # 録画開始 (param4: ログ用メッセージ)
    REC_END = 103  # 録画終了 (param4: ログ用メッセージ)
    REC_TUIJYU = 104  # 録画中に追従が発生 (param4: ログ用メッセージ)
    CHG_TUIJYU = 105  # 追従が発生 (param4: ログ用メッセージ)
    PRE_EPGCAP_START = 106  # EPG 取得準備開始
    EPGCAP_START = 107  # EPG 取得開始
    EPGCAP_END = 108  # EPG 取得終了


class CtrlCmdUtil:
    """
    EpgTimerSrv の CtrlCmd インタフェースと通信する (EDCB/EpgTimer の CtrlCmd(Def).cs を移植したもの)

    ・利用可能なコマンドはもっとあるが使いそうなものだけ
    ・sendView* 系コマンドは EpgDataCap_Bon 等との通信用。接続先パイプは "View_Ctrl_BonNoWaitPipe_{プロセス ID}"
    """

    # EDCB の日付は OS のタイムゾーンに関わらず常に UTC+9
    TZ = datetime.timezone(datetime.timedelta(hours=9), 'JST')

    # 読み取った日付が不正なときや既定値に使う UNIX エポック
    UNIX_EPOCH = datetime.datetime(1970, 1, 1, 9, tzinfo=TZ)

    __connect_timeout_sec: float
    __pipe_name: str
    __host: str | None
    __port: int

    def __init__(self) -> None:
        self.__connect_timeout_sec = 15.
        self.__pipe_name = 'EpgTimerSrvNoWaitPipe'
        self.__host = None
        self.__port = 0

    def setPipeSetting(self, name: str) -> None:
        """ 名前付きパイプモードにする """
        self.__pipe_name = name
        self.__host = None

    def pipeExists(self) -> bool:
        """ 接続先パイプが存在するか調べる """
        try:
            with open('\\\\.\\pipe\\' + self.__pipe_name, mode='r+b'):
                pass
        except FileNotFoundError:
            return False
        except Exception:
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
        ret, _ = await self.__sendCmd(self.__CMD_VIEW_APP_SET_BONDRIVER,
                                      lambda buf: self.__writeString(buf, name))
        return ret == self.__CMD_SUCCESS

    async def sendViewGetBonDriver(self) -> str | None:
        """ 使用中の BonDriver のファイル名を取得 """
        ret, rbuf = await self.__sendCmd(self.__CMD_VIEW_APP_GET_BONDRIVER)
        if ret == self.__CMD_SUCCESS:
            try:
                return self.__readString(memoryview(rbuf), [0], len(rbuf))
            except self.__ReadError:
                pass
        return None

    async def sendViewSetCh(self, set_ch_info: SetChInfo) -> bool:
        """ チャンネル切り替え """
        ret, _ = await self.__sendCmd(self.__CMD_VIEW_APP_SET_CH,
                                      lambda buf: self.__writeSetChInfo(buf, set_ch_info))
        return ret == self.__CMD_SUCCESS

    async def sendViewAppClose(self) -> bool:
        """ アプリケーションの終了 """
        ret, _ = await self.__sendCmd(self.__CMD_VIEW_APP_CLOSE)
        return ret == self.__CMD_SUCCESS

    async def sendEnumService(self) -> list[ServiceInfo] | None:
        """ サービス一覧を取得する """
        ret, rbuf = await self.__sendCmd(self.__CMD_EPG_SRV_ENUM_SERVICE)
        if ret == self.__CMD_SUCCESS:
            try:
                return self.__readVector(self.__readServiceInfo, memoryview(rbuf), [0], len(rbuf))
            except self.__ReadError:
                pass
        return None

    async def sendEnumPgInfoEx(self, service_time_list: list[int]) -> list[ServiceEventInfo] | None:
        """
        サービス指定と時間指定で番組情報一覧を取得する

        引数の list の最終2要素で番組の開始時間の範囲、その他の要素でサービスを指定する。最終要素の
        1つ前は時間の始点、最終要素は時間の終点、それぞれ FILETIME 時間で指定する。その他の奇数イン
        デックス要素は (onid << 32 | tsid << 16 | sid) で表現するサービスの ID 、各々1つ手前の要素は
        比較対象のサービスの ID に対するビット OR マスクを指定する。
        """
        ret, rbuf = await self.__sendCmd(self.__CMD_EPG_SRV_ENUM_PG_INFO_EX,
                                         lambda buf: self.__writeVector(self.__writeLong, buf, service_time_list))
        if ret == self.__CMD_SUCCESS:
            try:
                return self.__readVector(self.__readServiceEventInfo, memoryview(rbuf), [0], len(rbuf))
            except self.__ReadError:
                pass
        return None

    async def sendEnumPgArc(self, service_time_list: list[int]) -> list[ServiceEventInfo] | None:
        """
        サービス指定と時間指定で過去番組情報一覧を取得する

        引数については sendEnumPgInfoEx() と同じ。このコマンドはファイルアクセスを伴うこと、また実装
        上の限界があることから、せいぜい1週間を目安に極端に大きな時間範囲を指定してはならない。
        """
        ret, rbuf = await self.__sendCmd(self.__CMD_EPG_SRV_ENUM_PG_ARC,
                                         lambda buf: self.__writeVector(self.__writeLong, buf, service_time_list))
        if ret == self.__CMD_SUCCESS:
            try:
                return self.__readVector(self.__readServiceEventInfo, memoryview(rbuf), [0], len(rbuf))
            except self.__ReadError:
                pass
        return None

    async def sendFileCopy(self, name: str) -> bytes | None:
        """ 指定ファイルを転送する """
        ret, rbuf = await self.__sendCmd(self.__CMD_EPG_SRV_FILE_COPY,
                                         lambda buf: self.__writeString(buf, name))
        if ret == self.__CMD_SUCCESS:
            return rbuf
        return None

    async def sendFileCopy2(self, name_list: list[str]) -> list[FileData] | None:
        """ 指定ファイルをまとめて転送する """
        ret, rbuf = await self.__sendCmd2(self.__CMD_EPG_SRV_FILE_COPY2,
                                          lambda buf: self.__writeVector(self.__writeString, buf, name_list))
        if ret == self.__CMD_SUCCESS:
            bufview = memoryview(rbuf)
            pos = [0]
            try:
                if self.__readUshort(bufview, pos, len(rbuf)) >= self.__CMD_VER:
                    return self.__readVector(self.__readFileData, bufview, pos, len(rbuf))
            except self.__ReadError:
                pass
        return None

    async def sendNwTVIDSetCh(self, set_ch_info: SetChInfo) -> int | None:
        """ NetworkTV モードの View アプリのチャンネルを切り替え、または起動の確認 (ID 指定) """
        ret, rbuf = await self.__sendCmd(self.__CMD_EPG_SRV_NWTV_ID_SET_CH,
                                         lambda buf: self.__writeSetChInfo(buf, set_ch_info))
        if ret == self.__CMD_SUCCESS:
            try:
                return self.__readInt(memoryview(rbuf), [0], len(rbuf))
            except self.__ReadError:
                pass
        return None

    async def sendNwTVIDClose(self, nwtv_id: int) -> bool:
        """ NetworkTV モードで起動中の View アプリを終了 (ID 指定) """
        ret, _ = await self.__sendCmd(self.__CMD_EPG_SRV_NWTV_ID_CLOSE,
                                      lambda buf: self.__writeInt(buf, nwtv_id))
        return ret == self.__CMD_SUCCESS

    async def sendEnumReserve(self) -> list[ReserveData] | None:
        """ 予約一覧を取得する """
        ret, rbuf = await self.__sendCmd2(self.__CMD_EPG_SRV_ENUM_RESERVE2)
        if ret == self.__CMD_SUCCESS:
            bufview = memoryview(rbuf)
            pos = [0]
            try:
                if self.__readUshort(bufview, pos, len(rbuf)) >= self.__CMD_VER:
                    return self.__readVector(self.__readReserveData, bufview, pos, len(rbuf))
            except self.__ReadError:
                pass
        return None

    async def sendAddReserve(self, reserve_list: list[ReserveData]) -> bool:
        """ 予約を追加する """
        ret, _ = await self.__sendCmd2(self.__CMD_EPG_SRV_ADD_RESERVE2,
                                       lambda buf: self.__writeVector(self.__writeReserveData, buf, reserve_list))
        return ret == self.__CMD_SUCCESS

    async def sendChgReserve(self, reserve_list: list[ReserveData]) -> bool:
        """ 予約を変更する """
        ret, _ = await self.__sendCmd2(self.__CMD_EPG_SRV_CHG_RESERVE2,
                                       lambda buf: self.__writeVector(self.__writeReserveData, buf, reserve_list))
        return ret == self.__CMD_SUCCESS

    async def sendDelReserve(self, reserve_id_list: list[int]) -> bool:
        """ 予約を削除する """
        ret, _ = await self.__sendCmd(self.__CMD_EPG_SRV_DEL_RESERVE,
                                      lambda buf: self.__writeVector(self.__writeInt, buf, reserve_id_list))
        return ret == self.__CMD_SUCCESS

    async def sendEnumRecInfoBasic(self) -> list[RecFileInfo] | None:
        """ 録画済み情報一覧取得 (programInfo と errInfo を除く) """
        ret, rbuf = await self.__sendCmd2(self.__CMD_EPG_SRV_ENUM_RECINFO_BASIC2)
        if ret == self.__CMD_SUCCESS:
            bufview = memoryview(rbuf)
            pos = [0]
            try:
                if self.__readUshort(bufview, pos, len(rbuf)) >= self.__CMD_VER:
                    return self.__readVector(self.__readRecFileInfo, bufview, pos, len(rbuf))
            except self.__ReadError:
                pass
        return None

    async def sendGetRecInfo(self, info_id: int) -> RecFileInfo | None:
        """ 録画済み情報取得 """
        ret, rbuf = await self.__sendCmd2(self.__CMD_EPG_SRV_GET_RECINFO2,
                                          lambda buf: self.__writeInt(buf, info_id))
        if ret == self.__CMD_SUCCESS:
            bufview = memoryview(rbuf)
            pos = [0]
            try:
                if self.__readUshort(bufview, pos, len(rbuf)) >= self.__CMD_VER:
                    return self.__readRecFileInfo(bufview, pos, len(rbuf))
            except self.__ReadError:
                pass
        return None

    async def sendChgPathRecInfo(self, info_list: list[RecFileInfo]) -> bool:
        """ 録画済み情報のファイルパスを変更する """
        ret, _ = await self.__sendCmd(self.__CMD_EPG_SRV_CHG_PATH_RECINFO,
                                      lambda buf: self.__writeVector(self.__writeRecFileInfo, buf, info_list))
        return ret == self.__CMD_SUCCESS

    async def sendChgProtectRecInfo(self, info_list: list[RecFileInfo]) -> bool:
        """ 録画済み情報のプロテクト変更 """
        ret, _ = await self.__sendCmd2(self.__CMD_EPG_SRV_CHG_PROTECT_RECINFO2,
                                       lambda buf: self.__writeVector(self.__writeRecFileInfo2, buf, info_list))
        return ret == self.__CMD_SUCCESS

    async def sendDelRecInfo(self, id_list: list[int]) -> bool:
        """ 録画済み情報を削除する """
        ret, _ = await self.__sendCmd(self.__CMD_EPG_SRV_DEL_RECINFO,
                                      lambda buf: self.__writeVector(self.__writeInt, buf, id_list))
        return ret == self.__CMD_SUCCESS

    async def sendGetRecFileNetworkPath(self, path: str) -> str | None:
        """ 録画ファイルのネットワークパスを取得 (tkntrec 拡張) """
        ret, rbuf = await self.__sendCmd(self.__CMD_EPG_SRV_GET_NETWORK_PATH,
                                         lambda buf: self.__writeString(buf, path))
        if ret == self.__CMD_SUCCESS:
            try:
                return self.__readString(memoryview(rbuf), [0], len(rbuf))
            except self.__ReadError:
                pass
        return None

    async def sendEnumTunerReserve(self) -> list[TunerReserveInfo] | None:
        """ チューナーごとの予約一覧を取得する """
        ret, rbuf = await self.__sendCmd(self.__CMD_EPG_SRV_ENUM_TUNER_RESERVE)
        if ret == self.__CMD_SUCCESS:
            try:
                return self.__readVector(self.__readTunerReserveInfo, memoryview(rbuf), [0], len(rbuf))
            except self.__ReadError:
                pass
        return None

    async def sendEnumPlugIn(self, index: Literal[1, 2]) -> list[str] | None:
        """ PlugIn ファイルの一覧を取得する """
        ret, rbuf = await self.__sendCmd(self.__CMD_EPG_SRV_ENUM_PLUGIN,
                                         lambda buf: self.__writeUshort(buf, index))
        if ret == self.__CMD_SUCCESS:
            try:
                return self.__readVector(self.__readString, memoryview(rbuf), [0], len(rbuf))
            except self.__ReadError:
                pass
        return None

    async def sendSearchPg(self, key_list: list[SearchKeyInfo]) -> list[EventInfo] | None:
        """ 指定キーワードで番組情報を検索する """
        ret, rbuf = await self.__sendCmd(self.__CMD_EPG_SRV_SEARCH_PG,
                                         lambda buf: self.__writeVector(self.__writeSearchKeyInfo, buf, key_list))
        if ret == self.__CMD_SUCCESS:
            try:
                return self.__readVector(self.__readEventInfo, memoryview(rbuf), [0], len(rbuf))
            except self.__ReadError:
                pass
        return None

    async def sendEnumAutoAdd(self) -> list[AutoAddData] | None:
        """ 自動予約登録情報一覧を取得する """
        ret, rbuf = await self.__sendCmd2(self.__CMD_EPG_SRV_ENUM_AUTO_ADD2)
        if ret == self.__CMD_SUCCESS:
            bufview = memoryview(rbuf)
            pos = [0]
            try:
                if self.__readUshort(bufview, pos, len(rbuf)) >= self.__CMD_VER:
                    return self.__readVector(self.__readAutoAddData, bufview, pos, len(rbuf))
            except self.__ReadError:
                pass
        return None

    async def sendAddAutoAdd(self, data_list: list[AutoAddData]) -> bool:
        """ 自動予約登録情報を追加する """
        ret, _ = await self.__sendCmd2(self.__CMD_EPG_SRV_ADD_AUTO_ADD2,
                                       lambda buf: self.__writeVector(self.__writeAutoAddData, buf, data_list))
        return ret == self.__CMD_SUCCESS

    async def sendChgAutoAdd(self, data_list: list[AutoAddData]) -> bool:
        """ 自動予約登録情報を変更する """
        ret, _ = await self.__sendCmd2(self.__CMD_EPG_SRV_CHG_AUTO_ADD2,
                                       lambda buf: self.__writeVector(self.__writeAutoAddData, buf, data_list))
        return ret == self.__CMD_SUCCESS

    async def sendDelAutoAdd(self, id_list: list[int]) -> bool:
        """ 自動予約登録情報を削除する """
        ret, _ = await self.__sendCmd(self.__CMD_EPG_SRV_DEL_AUTO_ADD,
                                      lambda buf: self.__writeVector(self.__writeInt, buf, id_list))
        return ret == self.__CMD_SUCCESS

    async def sendEnumManualAdd(self) -> list[ManualAutoAddData] | None:
        """ 自動予約 (プログラム) 登録情報一覧を取得する """
        ret, rbuf = await self.__sendCmd2(self.__CMD_EPG_SRV_ENUM_MANU_ADD2)
        if ret == self.__CMD_SUCCESS:
            bufview = memoryview(rbuf)
            pos = [0]
            try:
                if self.__readUshort(bufview, pos, len(rbuf)) >= self.__CMD_VER:
                    return self.__readVector(self.__readManualAutoAddData, bufview, pos, len(rbuf))
            except self.__ReadError:
                pass
        return None

    async def sendAddManualAdd(self, data_list: list[ManualAutoAddData]) -> bool:
        """ 自動予約 (プログラム) 登録情報を追加する """
        ret, _ = await self.__sendCmd2(self.__CMD_EPG_SRV_ADD_MANU_ADD2,
                                       lambda buf: self.__writeVector(self.__writeManualAutoAddData, buf, data_list))
        return ret == self.__CMD_SUCCESS

    async def sendChgManualAdd(self, data_list: list[ManualAutoAddData]) -> bool:
        """ 自動予約 (プログラム) 登録情報を変更する """
        ret, _ = await self.__sendCmd2(self.__CMD_EPG_SRV_CHG_MANU_ADD2,
                                       lambda buf: self.__writeVector(self.__writeManualAutoAddData, buf, data_list))
        return ret == self.__CMD_SUCCESS

    async def sendDelManualAdd(self, id_list: list[int]) -> bool:
        """ 自動予約 (プログラム) 登録情報を削除する """
        ret, _ = await self.__sendCmd(self.__CMD_EPG_SRV_DEL_MANU_ADD,
                                      lambda buf: self.__writeVector(self.__writeInt, buf, id_list))
        return ret == self.__CMD_SUCCESS

    async def sendGetNotifySrvInfo(self, target_count: int) -> NotifySrvInfo | None:
        """ target_count より大きいカウントの通知を待つ (TCP/IP モードのときロングポーリング) """
        ret, rbuf = await self.__sendCmd2(self.__CMD_EPG_SRV_GET_STATUS_NOTIFY2,
                                          lambda buf: self.__writeUint(buf, target_count))
        if ret == self.__CMD_SUCCESS:
            bufview = memoryview(rbuf)
            pos = [0]
            try:
                if self.__readUshort(bufview, pos, len(rbuf)) >= self.__CMD_VER:
                    return self.__readNotifySrvInfo(bufview, pos, len(rbuf))
            except self.__ReadError:
                pass
        return None

    async def sendGetNotifySrvStatus(self) -> NotifySrvInfo | None:
        """ 現在の NotifyUpdate.SRV_STATUS を取得する """
        return await self.sendGetNotifySrvInfo(0)

    def openViewStream(self, process_id: int) -> socket.socket | None:
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
        except Exception:
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
        except Exception:
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
    __CMD_EPG_SRV_DEL_RESERVE = 1014
    __CMD_EPG_SRV_ENUM_TUNER_RESERVE = 1016
    __CMD_EPG_SRV_DEL_RECINFO = 1018
    __CMD_EPG_SRV_CHG_PATH_RECINFO = 1019
    __CMD_EPG_SRV_ENUM_SERVICE = 1021
    __CMD_EPG_SRV_SEARCH_PG = 1025
    __CMD_EPG_SRV_ENUM_PG_INFO_EX = 1029
    __CMD_EPG_SRV_ENUM_PG_ARC = 1030
    __CMD_EPG_SRV_DEL_AUTO_ADD = 1033
    __CMD_EPG_SRV_DEL_MANU_ADD = 1043
    __CMD_EPG_SRV_FILE_COPY = 1060
    __CMD_EPG_SRV_ENUM_PLUGIN = 1061
    __CMD_EPG_SRV_NWTV_ID_SET_CH = 1073
    __CMD_EPG_SRV_NWTV_ID_CLOSE = 1074
    __CMD_EPG_SRV_GET_NETWORK_PATH = 1299
    __CMD_EPG_SRV_ENUM_RESERVE2 = 2011
    __CMD_EPG_SRV_GET_RESERVE2 = 2012
    __CMD_EPG_SRV_ADD_RESERVE2 = 2013
    __CMD_EPG_SRV_CHG_RESERVE2 = 2015
    __CMD_EPG_SRV_CHG_PROTECT_RECINFO2 = 2019
    __CMD_EPG_SRV_ENUM_RECINFO_BASIC2 = 2020
    __CMD_EPG_SRV_GET_RECINFO2 = 2024
    __CMD_EPG_SRV_FILE_COPY2 = 2060
    __CMD_EPG_SRV_ENUM_AUTO_ADD2 = 2131
    __CMD_EPG_SRV_ADD_AUTO_ADD2 = 2132
    __CMD_EPG_SRV_CHG_AUTO_ADD2 = 2134
    __CMD_EPG_SRV_ENUM_MANU_ADD2 = 2141
    __CMD_EPG_SRV_ADD_MANU_ADD2 = 2142
    __CMD_EPG_SRV_CHG_MANU_ADD2 = 2144
    __CMD_EPG_SRV_GET_STATUS_NOTIFY2 = 2200

    async def __sendAndReceive(self, buf: bytearray) -> tuple[int | None, bytes]:
        to = time.monotonic() + self.__connect_timeout_sec
        if self.__host is None:
            # 名前付きパイプモード
            while True:
                try:
                    with open('\\\\.\\pipe\\' + self.__pipe_name, mode='r+b') as f:
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
                except Exception:
                    pass
                await asyncio.sleep(0.01)
                if time.monotonic() >= to:
                    break
            return None, b''

        # TCP/IP モード
        try:
            r, w = await asyncio.wait_for(asyncio.open_connection(self.__host, self.__port), max(to - time.monotonic(), 0.))
        except Exception:
            return None, b''
        try:
            w.write(buf)
            await asyncio.wait_for(w.drain(), max(to - time.monotonic(), 0.))
            ret = 0
            size = 8
            rbuf = await asyncio.wait_for(r.readexactly(8), max(to - time.monotonic(), 0.))
            if len(rbuf) == 8:
                bufview = memoryview(rbuf)
                pos = [0]
                ret = self.__readInt(bufview, pos, 8)
                size = self.__readInt(bufview, pos, 8)
                rbuf = await asyncio.wait_for(r.readexactly(size), max(to - time.monotonic(), 0.))
        except Exception:
            return None, b''
        finally:
            w.close()
        try:
            await asyncio.wait_for(w.wait_closed(), max(to - time.monotonic(), 0.))
        except Exception:
            pass
        if len(rbuf) == size:
            return ret, rbuf
        return None, b''

    async def __sendCmd(self, cmd: int, write_func: Callable[[bytearray], None] | None = None) -> tuple[int | None, bytes]:
        buf = bytearray()
        self.__writeInt(buf, cmd)
        self.__writeInt(buf, 0)
        if write_func:
            write_func(buf)
        self.__writeIntInplace(buf, 4, len(buf) - 8)
        return await self.__sendAndReceive(buf)

    async def __sendCmd2(self, cmd2: int, write_func: Callable[[bytearray], None] | None = None) -> tuple[int | None, bytes]:
        buf = bytearray()
        self.__writeInt(buf, cmd2)
        self.__writeInt(buf, 0)
        self.__writeUshort(buf, self.__CMD_VER)
        if write_func:
            write_func(buf)
        self.__writeIntInplace(buf, 4, len(buf) - 8)
        return await self.__sendAndReceive(buf)

    @staticmethod
    def __writeByte(buf: bytearray, v: int) -> None:
        buf.extend(v.to_bytes(1, 'little'))

    @staticmethod
    def __writeUshort(buf: bytearray, v: int) -> None:
        buf.extend(v.to_bytes(2, 'little'))

    @staticmethod
    def __writeInt(buf: bytearray, v: int) -> None:
        buf.extend(v.to_bytes(4, 'little', signed=True))

    @staticmethod
    def __writeUint(buf: bytearray, v: int) -> None:
        buf.extend(v.to_bytes(4, 'little'))

    @staticmethod
    def __writeLong(buf: bytearray, v: int) -> None:
        buf.extend(v.to_bytes(8, 'little', signed=True))

    @staticmethod
    def __writeIntInplace(buf: bytearray, pos: int, v: int) -> None:
        buf[pos:pos + 4] = v.to_bytes(4, 'little', signed=True)

    @classmethod
    def __writeSystemTime(cls, buf: bytearray, v: datetime.datetime) -> None:
        cls.__writeUshort(buf, v.year)
        cls.__writeUshort(buf, v.month)
        cls.__writeUshort(buf, v.isoweekday() % 7)
        cls.__writeUshort(buf, v.day)
        cls.__writeUshort(buf, v.hour)
        cls.__writeUshort(buf, v.minute)
        cls.__writeUshort(buf, v.second)
        cls.__writeUshort(buf, 0)

    @classmethod
    def __writeString(cls, buf: bytearray, v: str) -> None:
        vv = v.encode('utf_16_le')
        cls.__writeInt(buf, 6 + len(vv))
        buf.extend(vv)
        cls.__writeUshort(buf, 0)

    @classmethod
    def __writeVector(cls, write_func: Callable[[bytearray, T], None], buf: bytearray, v: list[T]) -> None:
        pos = len(buf)
        cls.__writeInt(buf, 0)
        cls.__writeInt(buf, len(v))
        for e in v:
            write_func(buf, e)
        cls.__writeIntInplace(buf, pos, len(buf) - pos)

    # 以下、各構造体のライター

    @classmethod
    def __writeSetChInfo(cls, buf: bytearray, v: SetChInfo) -> None:
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

    @classmethod
    def __writeRecFileSetInfo(cls, buf: bytearray, v: RecFileSetInfo) -> None:
        pos = len(buf)
        cls.__writeInt(buf, 0)
        cls.__writeString(buf, v.get('rec_folder', ''))
        cls.__writeString(buf, v.get('write_plug_in', ''))
        cls.__writeString(buf, v.get('rec_name_plug_in', ''))
        cls.__writeString(buf, '')
        cls.__writeIntInplace(buf, pos, len(buf) - pos)

    @classmethod
    def __writeRecSettingData(cls, buf: bytearray, v: RecSettingData) -> None:
        pos = len(buf)
        cls.__writeInt(buf, 0)
        cls.__writeByte(buf, v.get('rec_mode', 0))
        cls.__writeByte(buf, v.get('priority', 0))
        cls.__writeByte(buf, v.get('tuijyuu_flag', False))
        cls.__writeUint(buf, v.get('service_mode', 0))
        cls.__writeByte(buf, v.get('pittari_flag', False))
        cls.__writeString(buf, v.get('bat_file_path', ''))
        cls.__writeVector(cls.__writeRecFileSetInfo, buf, v.get('rec_folder_list', []))
        cls.__writeByte(buf, v.get('suspend_mode', 0))
        cls.__writeByte(buf, v.get('reboot_flag', False))
        cls.__writeByte(buf, v.get('start_margin') is not None and v.get('end_margin') is not None)
        cls.__writeInt(buf, v.get('start_margin', 0))
        cls.__writeInt(buf, v.get('end_margin', 0))
        cls.__writeByte(buf, v.get('continue_rec_flag', False))
        cls.__writeByte(buf, v.get('partial_rec_flag', 0))
        cls.__writeUint(buf, v.get('tuner_id', 0))
        cls.__writeVector(cls.__writeRecFileSetInfo, buf, v.get('partial_rec_folder', []))
        cls.__writeIntInplace(buf, pos, len(buf) - pos)

    @classmethod
    def __writeReserveData(cls, buf: bytearray, v: ReserveData) -> None:
        pos = len(buf)
        cls.__writeInt(buf, 0)
        cls.__writeString(buf, v.get('title', ''))
        cls.__writeSystemTime(buf, v.get('start_time', cls.UNIX_EPOCH))
        cls.__writeUint(buf, v.get('duration_second', 0))
        cls.__writeString(buf, v.get('station_name', ''))
        cls.__writeUshort(buf, v.get('onid', 0))
        cls.__writeUshort(buf, v.get('tsid', 0))
        cls.__writeUshort(buf, v.get('sid', 0))
        cls.__writeUshort(buf, v.get('eid', 0))
        cls.__writeString(buf, v.get('comment', ''))
        cls.__writeInt(buf, v.get('reserve_id', 0))
        cls.__writeByte(buf, 0)
        cls.__writeByte(buf, v.get('overlap_mode', 0))
        cls.__writeString(buf, '')
        cls.__writeSystemTime(buf, v.get('start_time_epg', cls.UNIX_EPOCH))
        cls.__writeRecSettingData(buf, v.get('rec_setting', {}))
        cls.__writeInt(buf, 0)
        cls.__writeVector(cls.__writeString, buf, v.get('rec_file_name_list', []))
        cls.__writeInt(buf, 0)
        cls.__writeIntInplace(buf, pos, len(buf) - pos)

    @classmethod
    def __writeRecFileInfo(cls, buf: bytearray, v: RecFileInfo, has_protect_flag: bool = False) -> None:
        pos = len(buf)
        cls.__writeInt(buf, 0)
        cls.__writeInt(buf, v.get('id', 0))
        cls.__writeString(buf, v.get('rec_file_path', ''))
        cls.__writeString(buf, v.get('title', ''))
        cls.__writeSystemTime(buf, v.get('start_time', cls.UNIX_EPOCH))
        cls.__writeUint(buf, v.get('duration_sec', 0))
        cls.__writeString(buf, v.get('service_name', ''))
        cls.__writeUshort(buf, v.get('onid', 0))
        cls.__writeUshort(buf, v.get('tsid', 0))
        cls.__writeUshort(buf, v.get('sid', 0))
        cls.__writeUshort(buf, v.get('eid', 0))
        cls.__writeLong(buf, v.get('drops', 0))
        cls.__writeLong(buf, v.get('scrambles', 0))
        cls.__writeInt(buf, v.get('rec_status', 0))
        cls.__writeSystemTime(buf, v.get('start_time_epg', cls.UNIX_EPOCH))
        cls.__writeString(buf, v.get('comment', ''))
        cls.__writeString(buf, v.get('program_info', ''))
        cls.__writeString(buf, v.get('err_info', ''))
        if has_protect_flag:
            cls.__writeByte(buf, v.get('protect_flag', False))
        cls.__writeIntInplace(buf, pos, len(buf) - pos)

    @classmethod
    def __writeRecFileInfo2(cls, buf: bytearray, v: RecFileInfo) -> None:
        cls.__writeRecFileInfo(buf, v, True)

    @classmethod
    def __writeContentData(cls, buf: bytearray, v: ContentData) -> None:
        pos = len(buf)
        cls.__writeInt(buf, 0)
        cn = v.get('content_nibble', 0)
        un = v.get('user_nibble', 0)
        cls.__writeUshort(buf, (cn >> 8 | cn << 8) & 0xffff)
        cls.__writeUshort(buf, (un >> 8 | un << 8) & 0xffff)
        cls.__writeIntInplace(buf, pos, len(buf) - pos)

    @classmethod
    def __writeSearchDateInfo(cls, buf: bytearray, v: SearchDateInfo) -> None:
        pos = len(buf)
        cls.__writeInt(buf, 0)
        cls.__writeByte(buf, v.get('start_day_of_week', 0))
        cls.__writeUshort(buf, v.get('start_hour', 0))
        cls.__writeUshort(buf, v.get('start_min', 0))
        cls.__writeByte(buf, v.get('end_day_of_week', 0))
        cls.__writeUshort(buf, v.get('end_hour', 0))
        cls.__writeUshort(buf, v.get('end_min', 0))
        cls.__writeIntInplace(buf, pos, len(buf) - pos)

    @classmethod
    def __writeSearchKeyInfo(cls, buf: bytearray, v: SearchKeyInfo, has_chk_rec_end: bool = False) -> None:
        pos = len(buf)
        cls.__writeInt(buf, 0)
        chk_duration = int(v.get('chk_duration_min', 0) * 10000 + v.get('chk_duration_max', 0)) % 100000000
        cls.__writeString(buf, ('^!{999}' if v.get('key_disabled', False) else '') +
                               ('C!{999}' if v.get('case_sensitive', False) else '') +
                               (f'D!{{1{chk_duration:08d}}}' if chk_duration > 0 else '') + v.get('and_key', ''))
        cls.__writeString(buf, v.get('not_key', ''))
        cls.__writeInt(buf, v.get('reg_exp_flag', False))
        cls.__writeInt(buf, v.get('title_only_flag', False))
        cls.__writeVector(cls.__writeContentData, buf, v.get('content_list', []))
        cls.__writeVector(cls.__writeSearchDateInfo, buf, v.get('date_list', []))
        cls.__writeVector(cls.__writeLong, buf, v.get('service_list', []))
        cls.__writeVector(cls.__writeUshort, buf, v.get('video_list', []))
        cls.__writeVector(cls.__writeUshort, buf, v.get('audio_list', []))
        cls.__writeByte(buf, v.get('aimai_flag', False))
        cls.__writeByte(buf, v.get('not_contet_flag', False))
        cls.__writeByte(buf, v.get('not_date_flag', False))
        cls.__writeByte(buf, v.get('free_ca_flag', 0))
        if has_chk_rec_end:
            cls.__writeByte(buf, v.get('chk_rec_end', False))
            chk_rec_day = v.get('chk_rec_day', 0)
            cls.__writeUshort(buf, chk_rec_day % 10000 + 40000 if v.get('chk_rec_no_service', False) else chk_rec_day)
        cls.__writeIntInplace(buf, pos, len(buf) - pos)

    @classmethod
    def __writeSearchKeyInfo2(cls, buf: bytearray, v: SearchKeyInfo) -> None:
        cls.__writeSearchKeyInfo(buf, v, True)

    @classmethod
    def __writeAutoAddData(cls, buf: bytearray, v: AutoAddData) -> None:
        pos = len(buf)
        cls.__writeInt(buf, 0)
        cls.__writeInt(buf, v.get('data_id', 0))
        cls.__writeSearchKeyInfo2(buf, v.get('search_info', {}))
        cls.__writeRecSettingData(buf, v.get('rec_setting', {}))
        cls.__writeInt(buf, v.get('add_count', 0))
        cls.__writeIntInplace(buf, pos, len(buf) - pos)

    @classmethod
    def __writeManualAutoAddData(cls, buf: bytearray, v: ManualAutoAddData) -> None:
        pos = len(buf)
        cls.__writeInt(buf, 0)
        cls.__writeInt(buf, v.get('data_id', 0))
        cls.__writeByte(buf, v.get('day_of_week_flag', 0))
        cls.__writeUint(buf, v.get('start_time', 0))
        cls.__writeUint(buf, v.get('duration_second', 0))
        cls.__writeString(buf, v.get('title', ''))
        cls.__writeString(buf, v.get('station_name', ''))
        cls.__writeUshort(buf, v.get('onid', 0))
        cls.__writeUshort(buf, v.get('tsid', 0))
        cls.__writeUshort(buf, v.get('sid', 0))
        cls.__writeRecSettingData(buf, v.get('rec_setting', {}))
        cls.__writeIntInplace(buf, pos, len(buf) - pos)

    class __ReadError(Exception):
        """ バッファをデータ構造として読み取るのに失敗したときの内部エラー """
        pass

    @classmethod
    def __readByte(cls, buf: memoryview, pos: list[int], size: int) -> int:
        if size - pos[0] < 1:
            raise cls.__ReadError
        v = buf[pos[0]]
        pos[0] += 1
        return v

    @classmethod
    def __readUshort(cls, buf: memoryview, pos: list[int], size: int) -> int:
        if size - pos[0] < 2:
            raise cls.__ReadError
        v = buf[pos[0]] | buf[pos[0] + 1] << 8
        pos[0] += 2
        return v

    @classmethod
    def __readInt(cls, buf: memoryview, pos: list[int], size: int) -> int:
        if size - pos[0] < 4:
            raise cls.__ReadError
        v = int.from_bytes(buf[pos[0]:pos[0] + 4], 'little', signed=True)
        pos[0] += 4
        return v

    @classmethod
    def __readUint(cls, buf: memoryview, pos: list[int], size: int) -> int:
        if size - pos[0] < 4:
            raise cls.__ReadError
        v = int.from_bytes(buf[pos[0]:pos[0] + 4], 'little')
        pos[0] += 4
        return v

    @classmethod
    def __readLong(cls, buf: memoryview, pos: list[int], size: int) -> int:
        if size - pos[0] < 8:
            raise cls.__ReadError
        v = int.from_bytes(buf[pos[0]:pos[0] + 8], 'little', signed=True)
        pos[0] += 8
        return v

    @classmethod
    def __readSystemTime(cls, buf: memoryview, pos: list[int], size: int) -> datetime.datetime:
        if size - pos[0] < 16:
            raise cls.__ReadError
        try:
            pos0 = pos[0]
            v = datetime.datetime(buf[pos0] | buf[pos0 + 1] << 8,
                                  buf[pos0 + 2] | buf[pos0 + 3] << 8,
                                  buf[pos0 + 6] | buf[pos0 + 7] << 8,
                                  buf[pos0 + 8] | buf[pos0 + 9] << 8,
                                  buf[pos0 + 10] | buf[pos0 + 11] << 8,
                                  buf[pos0 + 12] | buf[pos0 + 13] << 8,
                                  tzinfo=cls.TZ)
        except Exception:
            v = cls.UNIX_EPOCH
        pos[0] += 16
        return v

    @classmethod
    def __readString(cls, buf: memoryview, pos: list[int], size: int) -> str:
        vs = cls.__readInt(buf, pos, size)
        if vs < 6 or size - pos[0] < vs - 4:
            raise cls.__ReadError
        v = str(buf[pos[0]:pos[0] + vs - 6], 'utf_16_le')
        pos[0] += vs - 4
        return v

    @classmethod
    def __readVector(cls, read_func: Callable[[memoryview, list[int], int], T], buf: memoryview, pos: list[int], size: int) -> list[T]:
        vs = cls.__readInt(buf, pos, size)
        vc = cls.__readInt(buf, pos, size)
        if vs < 8 or vc < 0 or size - pos[0] < vs - 8:
            raise cls.__ReadError
        size = pos[0] + vs - 8
        v: list[T] = []
        for i in range(vc):
            v.append(read_func(buf, pos, size))
        pos[0] = size
        return v

    @classmethod
    def __readStructIntro(cls, buf: memoryview, pos: list[int], size: int) -> int:
        vs = cls.__readInt(buf, pos, size)
        if vs < 4 or size - pos[0] < vs - 4:
            raise cls.__ReadError
        return pos[0] + vs - 4

    # 以下、各構造体のリーダー

    @classmethod
    def __readFileData(cls, buf: memoryview, pos: list[int], size: int) -> FileData:
        size = cls.__readStructIntro(buf, pos, size)
        name = cls.__readString(buf, pos, size)
        data_size = cls.__readInt(buf, pos, size)
        cls.__readInt(buf, pos, size)
        if data_size < 0 or size - pos[0] < data_size:
            raise cls.__ReadError
        v: FileData = {
            'name': name,
            'data': bytes(buf[pos[0]:pos[0] + data_size])
        }
        pos[0] = size
        return v

    @classmethod
    def __readRecFileSetInfo(cls, buf: memoryview, pos: list[int], size: int) -> RecFileSetInfo:
        size = cls.__readStructIntro(buf, pos, size)
        v: RecFileSetInfo = {
            'rec_folder': cls.__readString(buf, pos, size),
            'write_plug_in': cls.__readString(buf, pos, size),
            'rec_name_plug_in': cls.__readString(buf, pos, size)
        }
        cls.__readString(buf, pos, size)
        pos[0] = size
        return v

    @classmethod
    def __readRecSettingData(cls, buf: memoryview, pos: list[int], size: int) -> RecSettingData:
        size = cls.__readStructIntro(buf, pos, size)
        v: RecSettingData = {
            'rec_mode': cls.__readByte(buf, pos, size),
            'priority': cls.__readByte(buf, pos, size),
            'tuijyuu_flag': cls.__readByte(buf, pos, size) != 0,
            'service_mode': cls.__readUint(buf, pos, size),
            'pittari_flag': cls.__readByte(buf, pos, size) != 0,
            'bat_file_path': cls.__readString(buf, pos, size),
            'rec_folder_list': cls.__readVector(cls.__readRecFileSetInfo, buf, pos, size),
            'suspend_mode': cls.__readByte(buf, pos, size),
            'reboot_flag': cls.__readByte(buf, pos, size) != 0
        }
        use_margin_flag = cls.__readByte(buf, pos, size) != 0
        start_margin = cls.__readInt(buf, pos, size)
        end_margin = cls.__readInt(buf, pos, size)
        if use_margin_flag:
            v['start_margin'] = start_margin
            v['end_margin'] = end_margin
        v['continue_rec_flag'] = cls.__readByte(buf, pos, size) != 0
        v['partial_rec_flag'] = cls.__readByte(buf, pos, size)
        v['tuner_id'] = cls.__readUint(buf, pos, size)
        v['partial_rec_folder'] = cls.__readVector(cls.__readRecFileSetInfo, buf, pos, size)
        pos[0] = size
        return v

    @classmethod
    def __readReserveData(cls, buf: memoryview, pos: list[int], size: int) -> ReserveData:
        size = cls.__readStructIntro(buf, pos, size)
        v: ReserveData = {
            'title': cls.__readString(buf, pos, size),
            'start_time': cls.__readSystemTime(buf, pos, size),
            'duration_second': cls.__readUint(buf, pos, size),
            'station_name': cls.__readString(buf, pos, size),
            'onid': cls.__readUshort(buf, pos, size),
            'tsid': cls.__readUshort(buf, pos, size),
            'sid': cls.__readUshort(buf, pos, size),
            'eid': cls.__readUshort(buf, pos, size),
            'comment': cls.__readString(buf, pos, size),
            'reserve_id': cls.__readInt(buf, pos, size)
        }
        cls.__readByte(buf, pos, size)
        v['overlap_mode'] = cls.__readByte(buf, pos, size)
        cls.__readString(buf, pos, size)
        v['start_time_epg'] = cls.__readSystemTime(buf, pos, size)
        v['rec_setting'] = cls.__readRecSettingData(buf, pos, size)
        cls.__readInt(buf, pos, size)
        v['rec_file_name_list'] = cls.__readVector(cls.__readString, buf, pos, size)
        cls.__readInt(buf, pos, size)
        pos[0] = size
        return v

    @classmethod
    def __readRecFileInfo(cls, buf: memoryview, pos: list[int], size: int) -> RecFileInfo:
        size = cls.__readStructIntro(buf, pos, size)
        v: RecFileInfo = {
            'id': cls.__readInt(buf, pos, size),
            'rec_file_path': cls.__readString(buf, pos, size),
            'title': cls.__readString(buf, pos, size),
            'start_time': cls.__readSystemTime(buf, pos, size),
            'duration_sec': cls.__readUint(buf, pos, size),
            'service_name': cls.__readString(buf, pos, size),
            'onid': cls.__readUshort(buf, pos, size),
            'tsid': cls.__readUshort(buf, pos, size),
            'sid': cls.__readUshort(buf, pos, size),
            'eid': cls.__readUshort(buf, pos, size),
            'drops': cls.__readLong(buf, pos, size),
            'scrambles': cls.__readLong(buf, pos, size),
            'rec_status': cls.__readInt(buf, pos, size),
            'start_time_epg': cls.__readSystemTime(buf, pos, size),
            'comment': cls.__readString(buf, pos, size),
            'program_info': cls.__readString(buf, pos, size),
            'err_info': cls.__readString(buf, pos, size),
            'protect_flag': cls.__readByte(buf, pos, size) != 0
        }
        pos[0] = size
        return v

    @classmethod
    def __readTunerReserveInfo(cls, buf: memoryview, pos: list[int], size: int) -> TunerReserveInfo:
        size = cls.__readStructIntro(buf, pos, size)
        v: TunerReserveInfo = {
            'tuner_id': cls.__readUint(buf, pos, size),
            'tuner_name': cls.__readString(buf, pos, size),
            'reserve_list': cls.__readVector(cls.__readInt, buf, pos, size)
        }
        pos[0] = size
        return v

    @classmethod
    def __readServiceEventInfo(cls, buf: memoryview, pos: list[int], size: int) -> ServiceEventInfo:
        size = cls.__readStructIntro(buf, pos, size)
        v: ServiceEventInfo = {
            'service_info': cls.__readServiceInfo(buf, pos, size),
            'event_list': cls.__readVector(cls.__readEventInfo, buf, pos, size)
        }
        pos[0] = size
        return v

    @classmethod
    def __readServiceInfo(cls, buf: memoryview, pos: list[int], size: int) -> ServiceInfo:
        size = cls.__readStructIntro(buf, pos, size)
        v: ServiceInfo = {
            'onid': cls.__readUshort(buf, pos, size),
            'tsid': cls.__readUshort(buf, pos, size),
            'sid': cls.__readUshort(buf, pos, size),
            'service_type': cls.__readByte(buf, pos, size),
            'partial_reception_flag': cls.__readByte(buf, pos, size),
            'service_provider_name': cls.__readString(buf, pos, size),
            'service_name': cls.__readString(buf, pos, size),
            'network_name': cls.__readString(buf, pos, size),
            'ts_name': cls.__readString(buf, pos, size),
            'remote_control_key_id': cls.__readByte(buf, pos, size)
        }
        pos[0] = size
        return v

    @classmethod
    def __readEventInfo(cls, buf: memoryview, pos: list[int], size: int) -> EventInfo:
        size = cls.__readStructIntro(buf, pos, size)
        v: EventInfo = {
            'onid': cls.__readUshort(buf, pos, size),
            'tsid': cls.__readUshort(buf, pos, size),
            'sid': cls.__readUshort(buf, pos, size),
            'eid': cls.__readUshort(buf, pos, size),
            'free_ca_flag': 0
        }

        start_time_flag = cls.__readByte(buf, pos, size)
        start_time = cls.__readSystemTime(buf, pos, size)
        if start_time_flag != 0:
            v['start_time'] = start_time

        duration_flag = cls.__readByte(buf, pos, size)
        duration_sec = cls.__readInt(buf, pos, size)
        if duration_flag != 0:
            v['duration_sec'] = duration_sec

        if cls.__readInt(buf, pos, size) != 4:
            pos[0] -= 4
            v['short_info'] = cls.__readShortEventInfo(buf, pos, size)

        if cls.__readInt(buf, pos, size) != 4:
            pos[0] -= 4
            v['ext_info'] = cls.__readExtendedEventInfo(buf, pos, size)

        if cls.__readInt(buf, pos, size) != 4:
            pos[0] -= 4
            v['content_info'] = cls.__readContentInfo(buf, pos, size)

        if cls.__readInt(buf, pos, size) != 4:
            pos[0] -= 4
            v['component_info'] = cls.__readComponentInfo(buf, pos, size)

        if cls.__readInt(buf, pos, size) != 4:
            pos[0] -= 4
            v['audio_info'] = cls.__readAudioComponentInfo(buf, pos, size)

        if cls.__readInt(buf, pos, size) != 4:
            pos[0] -= 4
            v['event_group_info'] = cls.__readEventGroupInfo(buf, pos, size)

        if cls.__readInt(buf, pos, size) != 4:
            pos[0] -= 4
            v['event_relay_info'] = cls.__readEventGroupInfo(buf, pos, size)

        v['free_ca_flag'] = cls.__readByte(buf, pos, size)
        pos[0] = size
        return v

    @classmethod
    def __readShortEventInfo(cls, buf: memoryview, pos: list[int], size: int) -> ShortEventInfo:
        size = cls.__readStructIntro(buf, pos, size)
        v: ShortEventInfo = {
            'event_name': cls.__readString(buf, pos, size),
            'text_char': cls.__readString(buf, pos, size)
        }
        pos[0] = size
        return v

    @classmethod
    def __readExtendedEventInfo(cls, buf: memoryview, pos: list[int], size: int) -> ExtendedEventInfo:
        size = cls.__readStructIntro(buf, pos, size)
        v: ExtendedEventInfo = {
            'text_char': cls.__readString(buf, pos, size)
        }
        pos[0] = size
        return v

    @classmethod
    def __readContentInfo(cls, buf: memoryview, pos: list[int], size: int) -> ContentInfo:
        size = cls.__readStructIntro(buf, pos, size)
        v: ContentInfo = {
            'nibble_list': cls.__readVector(cls.__readContentData, buf, pos, size)
        }
        pos[0] = size
        return v

    @classmethod
    def __readContentData(cls, buf: memoryview, pos: list[int], size: int) -> ContentData:
        size = cls.__readStructIntro(buf, pos, size)
        cn = cls.__readUshort(buf, pos, size)
        un = cls.__readUshort(buf, pos, size)
        v: ContentData = {
            'content_nibble': (cn >> 8 | cn << 8) & 0xffff,
            'user_nibble': (un >> 8 | un << 8) & 0xffff
        }
        pos[0] = size
        return v

    @classmethod
    def __readComponentInfo(cls, buf: memoryview, pos: list[int], size: int) -> ComponentInfo:
        size = cls.__readStructIntro(buf, pos, size)
        v: ComponentInfo = {
            'stream_content': cls.__readByte(buf, pos, size),
            'component_type': cls.__readByte(buf, pos, size),
            'component_tag': cls.__readByte(buf, pos, size),
            'text_char': cls.__readString(buf, pos, size)
        }
        pos[0] = size
        return v

    @classmethod
    def __readAudioComponentInfo(cls, buf: memoryview, pos: list[int], size: int) -> AudioComponentInfo:
        size = cls.__readStructIntro(buf, pos, size)
        v: AudioComponentInfo = {
            'component_list': cls.__readVector(cls.__readAudioComponentInfoData, buf, pos, size)
        }
        pos[0] = size
        return v

    @classmethod
    def __readAudioComponentInfoData(cls, buf: memoryview, pos: list[int], size: int) -> AudioComponentInfoData:
        size = cls.__readStructIntro(buf, pos, size)
        v: AudioComponentInfoData = {
            'stream_content': cls.__readByte(buf, pos, size),
            'component_type': cls.__readByte(buf, pos, size),
            'component_tag': cls.__readByte(buf, pos, size),
            'stream_type': cls.__readByte(buf, pos, size),
            'simulcast_group_tag': cls.__readByte(buf, pos, size),
            'es_multi_lingual_flag': cls.__readByte(buf, pos, size),
            'main_component_flag': cls.__readByte(buf, pos, size),
            'quality_indicator': cls.__readByte(buf, pos, size),
            'sampling_rate': cls.__readByte(buf, pos, size),
            'text_char': cls.__readString(buf, pos, size)
        }
        pos[0] = size
        return v

    @classmethod
    def __readEventGroupInfo(cls, buf: memoryview, pos: list[int], size: int) -> EventGroupInfo:
        size = cls.__readStructIntro(buf, pos, size)
        v: EventGroupInfo = {
            'group_type': cls.__readByte(buf, pos, size),
            'event_data_list': cls.__readVector(cls.__readEventData, buf, pos, size)
        }
        pos[0] = size
        return v

    @classmethod
    def __readEventData(cls, buf: memoryview, pos: list[int], size: int) -> EventData:
        size = cls.__readStructIntro(buf, pos, size)
        v: EventData = {
            'onid': cls.__readUshort(buf, pos, size),
            'tsid': cls.__readUshort(buf, pos, size),
            'sid': cls.__readUshort(buf, pos, size),
            'eid': cls.__readUshort(buf, pos, size)
        }
        pos[0] = size
        return v

    @classmethod
    def __readSearchDateInfo(cls, buf: memoryview, pos: list[int], size: int) -> SearchDateInfo:
        size = cls.__readStructIntro(buf, pos, size)
        v: SearchDateInfo = {
            'start_day_of_week': cls.__readByte(buf, pos, size),
            'start_hour': cls.__readUshort(buf, pos, size),
            'start_min': cls.__readUshort(buf, pos, size),
            'end_day_of_week': cls.__readByte(buf, pos, size),
            'end_hour': cls.__readUshort(buf, pos, size),
            'end_min': cls.__readUshort(buf, pos, size)
        }
        pos[0] = size
        return v

    @classmethod
    def __readSearchKeyInfo(cls, buf: memoryview, pos: list[int], size: int) -> SearchKeyInfo:
        size = cls.__readStructIntro(buf, pos, size)
        and_key = cls.__readString(buf, pos, size)
        key_disabled = and_key.startswith('^!{999}')
        and_key = and_key.removeprefix('^!{999}')
        case_sensitive = and_key.startswith('C!{999}')
        and_key = and_key.removeprefix('C!{999}')
        chk_duration_min = 0
        chk_duration_max = 0
        if (len(and_key) >= 13 and and_key.startswith('D!{1') and and_key[12] == '}' and
                all([c >= '0' and c <= '9' for c in and_key[4:12]])):
            chk_duration_max = int(and_key[3:12])
            and_key = and_key[13:]
            chk_duration_min = chk_duration_max // 10000 % 10000
            chk_duration_max = chk_duration_max % 10000
        v: SearchKeyInfo = {
            'and_key': and_key,
            'not_key': cls.__readString(buf, pos, size),
            'key_disabled': key_disabled,
            'case_sensitive': case_sensitive,
            'reg_exp_flag': cls.__readInt(buf, pos, size) != 0,
            'title_only_flag': cls.__readInt(buf, pos, size) != 0,
            'content_list': cls.__readVector(cls.__readContentData, buf, pos, size),
            'date_list': cls.__readVector(cls.__readSearchDateInfo, buf, pos, size),
            'service_list': cls.__readVector(cls.__readLong, buf, pos, size),
            'video_list': cls.__readVector(cls.__readUshort, buf, pos, size),
            'audio_list': cls.__readVector(cls.__readUshort, buf, pos, size),
            'aimai_flag': cls.__readByte(buf, pos, size) != 0,
            'not_contet_flag': cls.__readByte(buf, pos, size) != 0,
            'not_date_flag': cls.__readByte(buf, pos, size) != 0,
            'free_ca_flag': cls.__readByte(buf, pos, size),
            'chk_rec_end': cls.__readByte(buf, pos, size) != 0,
            'chk_duration_min': chk_duration_min,
            'chk_duration_max': chk_duration_max
        }
        chk_rec_day = cls.__readUshort(buf, pos, size)
        v['chk_rec_day'] = chk_rec_day % 10000 if chk_rec_day >= 40000 else chk_rec_day
        v['chk_rec_no_service'] = chk_rec_day >= 40000
        pos[0] = size
        return v

    @classmethod
    def __readAutoAddData(cls, buf: memoryview, pos: list[int], size: int) -> AutoAddData:
        size = cls.__readStructIntro(buf, pos, size)
        v: AutoAddData = {
            'data_id': cls.__readInt(buf, pos, size),
            'search_info': cls.__readSearchKeyInfo(buf, pos, size),
            'rec_setting': cls.__readRecSettingData(buf, pos, size),
            'add_count': cls.__readInt(buf, pos, size)
        }
        pos[0] = size
        return v

    @classmethod
    def __readManualAutoAddData(cls, buf: memoryview, pos: list[int], size: int) -> ManualAutoAddData:
        size = cls.__readStructIntro(buf, pos, size)
        v: ManualAutoAddData = {
            'data_id': cls.__readInt(buf, pos, size),
            'day_of_week_flag': cls.__readByte(buf, pos, size),
            'start_time': cls.__readUint(buf, pos, size),
            'duration_second': cls.__readUint(buf, pos, size),
            'title': cls.__readString(buf, pos, size),
            'station_name': cls.__readString(buf, pos, size),
            'onid': cls.__readUshort(buf, pos, size),
            'tsid': cls.__readUshort(buf, pos, size),
            'sid': cls.__readUshort(buf, pos, size),
            'rec_setting': cls.__readRecSettingData(buf, pos, size)
        }
        pos[0] = size
        return v

    @classmethod
    def __readNotifySrvInfo(cls, buf: memoryview, pos: list[int], size: int) -> NotifySrvInfo:
        size = cls.__readStructIntro(buf, pos, size)
        v: NotifySrvInfo = {
            'notify_id': cls.__readUint(buf, pos, size),
            'time': cls.__readSystemTime(buf, pos, size),
            'param1': cls.__readUint(buf, pos, size),
            'param2': cls.__readUint(buf, pos, size),
            'count': cls.__readUint(buf, pos, size),
            'param4': cls.__readString(buf, pos, size),
            'param5': cls.__readString(buf, pos, size),
            'param6': cls.__readString(buf, pos, size)
        }
        pos[0] = size
        return v
