# NEON AI (TM) SOFTWARE, Software Development Kit & Application Framework
# All trademark and other rights reserved by their respective owners
# Copyright 2008-2022 Neongecko.com Inc.
# Contributors: Daniel McKnight, Guy Daniels, Elon Gasper, Richard Leeds,
# Regina Bloomstine, Casimiro Ferreira, Andrii Pernatii, Kirill Hrymailo
# BSD-3 License
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
# 3. Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived from this
#    software without specific prior written permission.
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
# THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR
# CONTRIBUTORS  BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA,
# OR PROFITS;  OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE,  EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
# Copyright 2018 Mycroft AI Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import re
import random

from adapt.intent import IntentBuilder
from neon_utils.skills.neon_skill import NeonSkill, LOG
from os.path import join, exists
from threading import Lock

from mycroft.util import resolve_resource_file
from mycroft.skills.core import intent_handler
from mycroft.skills.audioservice import AudioService

STATUS_KEYS = ['track', 'artist', 'album', 'image']


class PlaybackControlSkill(NeonSkill):
    def __init__(self):
        super(PlaybackControlSkill, self).__init__('Playback Control Skill')
        self.query_replies = {}     # cache of received replies
        self.query_extensions = {}  # maintains query timeout extensions
        self.audio_service = None
        self.has_played = False
        self.lock = Lock()

    # TODO: Make this an option for voc_match()?  Only difference is the
    #       comparison using "==" instead of "in"
    def voc_match_exact(self, utt, voc_filename, lang=None):
        """ Determine if the given utterance contains the vocabulary provided

        Checks for vocabulary match in the utterance instead of the other
        way around to allow the user to say things like "yes, please" and
        still match against "Yes.voc" containing only "yes". The method first
        checks in the current skill's .voc files and secondly the "res/text"
        folder of mycroft-core. The result is cached to avoid hitting the
        disk each time the method is called.

        Args:
            utt (str): Utterance to be tested
            voc_filename (str): Name of vocabulary file (e.g. 'yes' for
                                'res/text/en-us/yes.voc')
            lang (str): Language code, defaults to self.long

        Returns:
            bool: True if the utterance has the given vocabulary it
        """
        lang = lang or self.lang
        cache_key = lang + voc_filename

        if cache_key not in self.voc_match_cache:
            # Check for both skill resources and mycroft-core resources
            voc = self.find_resource(voc_filename + '.voc', 'vocab')
            if not voc:
                voc = resolve_resource_file(join('text', lang,
                                                 voc_filename + '.voc'))

            if not voc or not exists(voc):
                raise FileNotFoundError(
                        'Could not find {}.voc file'.format(voc_filename))

            with open(voc) as f:
                self.voc_match_cache[cache_key] = f.read().splitlines()

        # Check for exact match
        if utt and any(i.strip() == utt
                       for i in self.voc_match_cache[cache_key]):
            return True
        return False

    def initialize(self):
        self.audio_service = AudioService(self.bus)
        self.add_event('play:query.response',
                       self.handle_play_query_response)
        self.add_event('play:status',
                       self.handle_song_info)
        self.gui.register_handler('next', self.handle_next)
        self.gui.register_handler('prev', self.handle_prev)

        self.clear_gui_info()
    # Handle common audio intents.  'Audio' skills should listen for the
    # common messages:
    #   self.add_event('mycroft.audio.service.next', SKILL_HANDLER)
    #   self.add_event('mycroft.audio.service.prev', SKILL_HANDLER)
    #   self.add_event('mycroft.audio.service.pause', SKILL_HANDLER)
    #   self.add_event('mycroft.audio.service.resume', SKILL_HANDLER)

    def clear_gui_info(self):
        """Clear the gui variable list."""
        # Initialize track info variables
        for k in STATUS_KEYS:
            self.gui[k] = ''

    @intent_handler(IntentBuilder('NextTrack').require('Next').require("Track"))
    def handle_next(self, message):
        self.audio_service.next()
        self.bus.emit(message.forward("playback_control.next"))

    @intent_handler(IntentBuilder('PrevTrack').require('Prev').require("Track"))
    def handle_prev(self, message):
        self.audio_service.prev()
        self.bus.emit(message.forward("playback_control.prev"))

    @intent_handler(IntentBuilder('Pause').require('Pause'))
    def handle_pause(self, message):
        self.audio_service.pause()
        self.bus.emit(message.forward("playback_control.pause"))

    @intent_handler(IntentBuilder('Play').one_of('PlayResume', 'Resume'))
    def handle_play(self, message):
        """Resume playback if paused"""
        self.audio_service.resume()
        self.bus.emit(message.forward("playback_control.resume"))

    def stop(self, message=None):
        self.clear_gui_info()
        self.gui.clear()
        LOG.info('Audio service status: '
                 '{}'.format(self.audio_service.track_info()))
        if self.audio_service.is_playing:
            self.audio_service.stop()
            return True
        else:
            return False

    def converse(self, message=None):
        utterances = message.data.get("utterances")
        if (utterances and self.has_played and
                self.voc_match_exact(utterances[0], "converse_resume")):
            # NOTE:  voc_match() will overmatch (e.g. it'll catch "play next
            #        song" or "play Some Artist")
            self.audio_service.resume()
            self.bus.emit(message.forward("playback_control.resume"))
            return True
        else:
            return False

    @intent_handler(IntentBuilder('').require('Play').require('Phrase'))
    def play(self, message):
        if self.check_for_signal("CORE_useHesitation", -1):
            self.speak_dialog('one_moment')
            # self.speak_dialog("just.one.moment")

        # Remove everything up to and including "Play"
        # NOTE: This requires a Play.voc which holds any synomyms for 'Play'
        #       and a .rx that contains each of those synonyms.  E.g.
        #  Play.voc
        #      play
        #      bork
        #  phrase.rx
        #      play (?P<Phrase>.*)
        #      bork (?P<Phrase>.*)
        # This really just hacks around limitations of the Adapt regex system,
        # which will only return the first word of the target phrase
        utt = message.data.get('utterance')
        phrase = re.sub('^.*?' + message.data['Play'], '', utt).strip()
        LOG.info("Resolving Player for: "+phrase)
        # wait_while_speaking()
        # self.enclosure.mouth_think()

        # Now we place a query on the messsagebus for anyone who wants to
        # attempt to service a 'play.request' message.  E.g.:
        #   {
        #      "type": "play.query",
        #      "phrase": "the news" / "tom waits" / "madonna on Pandora"
        #   }
        #
        # One or more skills can reply with a 'play.request.reply', e.g.:
        #   {
        #      "type": "play.request.response",
        #      "target": "the news",
        #      "skill_id": "<self.skill_id>",
        #      "conf": "0.7",
        #      "callback_data": "<optional data>"
        #   }
        # This means the skill has a 70% confidence they can handle that
        # request.  The "callback_data" is optional, but can provide data
        # that eliminates the need to re-parse if this reply is chosen.
        #
        self.query_replies[phrase] = []
        self.query_extensions[phrase] = []
        self.bus.emit(message.forward('play:query', data={"phrase": phrase}))

        self.schedule_event(self._play_query_timeout, 1,
                            data={"phrase": phrase},
                            name='PlayQueryTimeout')

    def handle_play_query_response(self, message):
        with self.lock:
            search_phrase = message.data["phrase"]

            if ("searching" in message.data and
                    search_phrase in self.query_extensions):
                # Manage requests for time to complete searches
                skill_id = message.data["skill_id"]
                if message.data["searching"]:
                    # extend the timeout by 5 seconds
                    self.cancel_scheduled_event("PlayQueryTimeout")
                    self.schedule_event(self._play_query_timeout, 5,
                                        data={"phrase": search_phrase},
                                        name='PlayQueryTimeout')

                    # TODO: Perhaps block multiple extensions?
                    if skill_id not in self.query_extensions[search_phrase]:
                        self.query_extensions[search_phrase].append(skill_id)
                else:
                    # Search complete, don't wait on this skill any longer
                    if skill_id in self.query_extensions[search_phrase]:
                        self.query_extensions[search_phrase].remove(skill_id)
                        if not self.query_extensions[search_phrase]:
                            self.cancel_scheduled_event("PlayQueryTimeout")
                            self.schedule_event(self._play_query_timeout, 1,
                                                data={"phrase": search_phrase},
                                                name='PlayQueryTimeout')

            elif search_phrase in self.query_replies:
                # Collect all replies until the timeout
                self.query_replies[message.data["phrase"]].append(message.data)

                skill_id = message.data["skill_id"]
                # Search complete, don't wait on this skill any longer
                if skill_id in self.query_extensions[search_phrase]:
                    self.query_extensions[search_phrase].remove(skill_id)
                    if not self.query_extensions[search_phrase]:
                        self.cancel_scheduled_event("PlayQueryTimeout")
                        self.schedule_event(self._play_query_timeout, 0,
                                            data={"phrase": search_phrase},
                                            name='PlayQueryTimeout')

    def _play_query_timeout(self, message):
        with self.lock:
            # Prevent any late-comers from retriggering this query handler
            search_phrase = message.data["phrase"]
            self.query_extensions[search_phrase] = []
            # self.enclosure.mouth_reset()

            # Look at any replies that arrived before the timeout
            # Find response(s) with the highest confidence
            best = None
            ties = []
            LOG.debug("CommonPlay Resolution: {}".format(search_phrase))
            for handler in self.query_replies[search_phrase]:
                LOG.debug("    {} using {}".format(handler["conf"],
                                                   handler["skill_id"]))
                if not best or handler["conf"] > best["conf"]:
                    best = handler
                    ties = []
                elif handler["conf"] == best["conf"]:
                    ties.append(handler)

            if best:
                if ties:
                    # select randomly
                    LOG.info("Skills tied, choosing randomly")
                    skills = ties + [best]
                    LOG.debug("Skills: " +
                              str([s["skill_id"] for s in skills]))
                    selected = random.choice(skills)
                    # TODO: Ask user to pick between ties or do it
                    # automagically
                else:
                    selected = best

                # invoke best match

                # If skill specified it has its own gui, don't use the generic one
                if not best.get("callback_data", {}).get("skill_gui", False):
                    self.gui.show_page("controls.qml", override_idle=True)
                LOG.info("Playing with: {}".format(selected["skill_id"]))
                start_data = {"skill_id": selected["skill_id"],
                              "phrase": search_phrase,
                              "callback_data": selected.get("callback_data")}
                self.bus.emit(message.forward('play:start', start_data))
                self.has_played = True
            # elif self.voc_match(search_phrase, "Music"):
            #     self.speak_dialog("setup.hints")
            else:
                LOG.info("   No matches")

                if self.neon_in_request(message):
                    # Notify
                    self.speak_dialog("cant.play", data={"phrase": search_phrase})

            if search_phrase in self.query_replies:
                del self.query_replies[search_phrase]
            if search_phrase in self.query_extensions:
                del self.query_extensions[search_phrase]

    def handle_song_info(self, message):
        changed = False
        for key in STATUS_KEYS:
            val = message.data.get(key, '')
            try:
                changed = changed or self.gui[key] != val
            except KeyError:
                changed = True
            self.gui[key] = val

        if changed:
            LOG.info('\n-->Track: {}\n-->Artist: {}\n-->Image: {}'
                     ''.format(self.gui['track'], self.gui['artist'],
                               self.gui['image']))


def create_skill():
    return PlaybackControlSkill()
