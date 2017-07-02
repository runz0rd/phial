from slackclient import SlackClient
import time
import re
from .globals import _command_ctx_stack
from .wrappers import Command, Response, Message


class Phial():
    '''
    The Phial object acts as the central object. It is given a token and an
    optional config dictionary. Once created it holds the core components of
    the bot, including command functions, command patterns and more.
    '''

    #: Default configuration
    default_config = {
        'prefix': '!',
        'read_websocket_delay': 1
    }

    def __init__(self, token, config=default_config):
        self.slack_client = SlackClient(token)
        self.commands = []
        self.command_functions = {}
        self.config = config
        self.running = False

    @staticmethod
    def _build_command_pattern(command):
        '''Creates the command pattern regexs'''
        command_regex = re.sub(r'(<\w+>)', r'(?P\1.+)', command)
        return re.compile("^{}$".format(command_regex))

    @staticmethod
    def _get_base_command(command):
        '''Gets the root part of the command'''
        return command.split(" ")[0]

    def add_command(self, command_pattern_template, command_func):
        '''
        Creates a command pattern and adds a command function to the bot. This
        is the same as :meth:`command`.

        ::

            @bot.command('hello')
            def world():
                pass

        Is the same as ::

            def world():
                pass
            bot.add_command('hello', world)

        Args:
            command_pattern_template(str): A string that will be used to create
                                           a command_pattern regex
            command_func(func): The fucntion to be run when the command pattern
                                is matched
        Raises:
            ValueError
                If command with the same name already registered
        '''
        command_pattern = self._build_command_pattern(command_pattern_template)
        base_command = self._get_base_command(command_pattern_template)
        if base_command not in self.command_functions:
            self.command_functions[base_command] = command_func
            self.commands.append((command_pattern, base_command))
        else:
            raise ValueError('Command {0} already exists'.format(base_command))

    def get_command_match(self, text):
        '''
        Returns a dictionary of args and the base command for the command
        pattern that is matched.
        Will returns None if no match

        Args:
            text(str): The string to be matched to a command

        Returns:
            A :obj:`dict` object with kwargs and the base command if a match
            is found otherwise :obj:`None`
        '''
        for command_pattern, base_command in self.commands:
            m = command_pattern.match(text)
            if m:
                return m.groupdict(), base_command
        return None

    def command(self, command_pattern_template):
        '''
        A decorator that is used to register a command function for a gievn
        command. This does the same as :meth:`add_command` but is used as a
        decorator.

        Args:
            command_pattern_template(str): A string that will be used to create
                                           a command_pattern regex

        Example:
            ::

                @bot.command('hello')
                def world():
                    pass

        '''
        def decorator(f):
            self.add_command(command_pattern_template, f)
            return f
        return decorator

    def _create_command(self, command_message):
        '''Creates an instance of a command'''
        command_match = self.get_command_match(command_message.text)
        if command_match:
            kwargs, base_command = command_match
            return Command(base_command,
                           command_message.channel,
                           kwargs,
                           command_message.user,
                           command_message.timestamp)
        else:
            raise ValueError('Command "{}" has not been registered'
                             .format(command_message.text))

    def _handle_command(self, command):
        '''Executes a given command'''
        try:
            _command_ctx_stack.push(command)
            return self.command_functions[command.base_command](**command
                                                                .args)
        finally:
            _command_ctx_stack.pop()

    def _parse_slack_output(self, slack_rtm_output):
        """
            The Slack Real Time Messaging API is an events firehose.
            this parsing function returns None unless a message is
            directed at the Bot, based on its ID.
        """
        output_list = slack_rtm_output
        if output_list and len(output_list) > 0:
            for output in output_list:
                if(output and 'text' in output and
                   output['text'].startswith(self.config['prefix'])):
                    return Message(output['text'][1:],
                                   output['channel'],
                                   output['user'],
                                   output['ts'])
        return None

    def send_message(self, message):
        '''
        Takes a response object and then sends the message to Slack

        Args:
            message(Response): message object to be sent to Slack

        '''
        if message.original_ts:
            self.slack_client.api_call("chat.postMessage",
                                       channel=message.channel,
                                       text=message.text,
                                       thread_ts=message.original_ts,
                                       as_user=True)
        else:
            self.slack_client.api_call("chat.postMessage",
                                       channel=message.channel,
                                       text=message.text,
                                       as_user=True)

    def send_reaction(self, response):
        '''
        Takes a response object and then sends the reaction to Slack

        Args:
            response(Response): response object conataining the reaction to be
                                sent to Slack

        '''
        self.slack_client.api_call("reactions.add",
                                   channel=response.channel,
                                   timestamp=response.original_ts,
                                   name=response.reaction,
                                   as_user=True)

    def _execute_response(self, response):
        '''Execute the response of a command function'''
        if not isinstance(response, Response):
            raise ValueError('Only Response objects can be excecuted')
        if response.original_ts and response.reaction and response.text:
            raise ValueError('Response objects with an original timestamp can '
                             + 'only have one of the attributes: Reaction, '
                             + 'Text')

        if response.original_ts and response.reaction:
            self.send_reaction(response)
        elif response.text:
            self.send_message(response)

    def is_running(self):
        return self.running

    def run(self):
        '''Connects to slack client and handles incoming messages'''
        self.running = True
        slack_client = self.slack_client
        if slack_client.rtm_connect():
            print("Phial connected and running!")
            while self.is_running():
                command_message = self._parse_slack_output(slack_client
                                                           .rtm_read())
                if command_message:
                    try:
                        command = self._create_command(command_message)
                        response = self._handle_command(command)
                        if response is not None:
                            self._execute_response(response)
                    except ValueError as err:
                        print('ValueError: {}'.format(err))
                time.sleep(self.config['read_websocket_delay'])
        else:
            raise ValueError("Connection failed. Invalid Token or bot ID")
