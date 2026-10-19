
from streamhub.utils import *

class XstreamPlayer(player):
    def __init__(self, *args, **kwargs):
        player.__init__(self, *args, **kwargs)
        self.streamFinished = False
        self.streamSuccess = True
        self.playedTime = 0
        self.totalTime = 999999
        self.from_global_search = False

    def onPlayBackStarted(self):
        log('VAVOO.TO -> [player]: starting Playback')
        try:
            self.totalTime = self.getTotalTime()
        except Exception:
            self.totalTime = 999999


        try:
            path = getInfoLabel('Container.FolderPath')
            if path:
                low = path.lower()
                keywords = [
                    'function=globalsearch',
                    'site=globalsearch',
                    'function=searchalter',
                    'function=searchtmdb'
                ]
                if any(kw in low for kw in keywords):
                    self.from_global_search = True
        except Exception:
            pass

    def onPlayBackStopped(self):
        if self.playedTime == 0 and self.totalTime == 999999:
            self.streamSuccess = False
        self.streamFinished = True


        if self.from_global_search:
            try:
                execute('Container.Update(plugin://%s/)' % addonID)
                log('VAVOO.TO -> [player]: Returning to addon main menu after Global Search')
            except Exception:
                log(format_exc())

    def onPlayBackEnded(self):
        self.onPlayBackStopped()

    def onPlayBackError(self):
        self.streamSuccess = False
        self.streamFinished = True


class cPlayer:
    def clearPlayList(self):
        oPlaylist = self.__getPlayList()
        oPlaylist.clear()

    def __getPlayList(self):
        return xbmc.PlayList(xbmc.PLAYLIST_VIDEO)

    def startPlayer(self):
        xbmcPlayer = XstreamPlayer()
        while not monitor.abortRequested() and not xbmcPlayer.streamFinished:
            try:
                if xbmcPlayer.isPlayingVideo():
                    xbmcPlayer.playedTime = xbmcPlayer.getTime()
            except RuntimeError:
                # Playback can stop between isPlayingVideo() and getTime().
                pass
            monitor.waitForAbort(1)
        return xbmcPlayer.streamSuccess
