#!/bin/python3
import argparse
import asyncio
import json
import logging
import re
import sys

from async_generator import async_generator, aclosing, yield_
from telethon import TelegramClient, events
from telethon.tl import types

logging.basicConfig(level=logging.WARNING)

FATHER = 'BotFather'
NEXT = chr(187)

NO_BOTS_MESSAGE = 'You have currently no bots'
MAX_BOTS_MESSAGE = 'That I cannot do.'


class Config:
    config_name = 'fathercli.json'
    session_name = 'fathercli'

    def __init__(self):
        try:
            with open(self.config_name) as f:
                self.__dict__ = json.load(f)
        except OSError:
            self.api_id = 0
            self.api_hash = ''
            self.bots = []

    def save(self):
        with open(self.config_name, 'w', encoding='utf-8') as f:
            json.dump(self.__dict__, f)

    def __setattr__(self, key, value):
        super().__setattr__(key, value)
        self.save()


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)
    quit(1)


def find_bot(config, query):
    clean = re.compile(r'[@\s]|(_?bot)$', re.IGNORECASE)
    q = clean.sub('', query).lower()
    for bot_id, bot_username in config.bots:
        bot_username = clean.sub('', bot_username).lower()
        if q in (str(bot_id), bot_username):
            return bot_id

    eprint('No bot found for ', q)


async def await_event(client, event, pre):
    message = asyncio.Future()

    @client.on(event)
    async def handler(ev):
        message.set_result(ev.message)

    await pre
    message = await message
    client.remove_event_handler(handler)
    return message


@async_generator
async def iter_buttons(client):
    message = await await_event(
        client,
        events.NewMessage(FATHER),
        client.send_message(FATHER, '/mybots')
    )

    done = not message.buttons
    while not done:
        done = True
        for row in message.buttons:
            for button in row:
                if button.text.startswith('@'):
                    await yield_(button)
                elif button.text == NEXT:
                    done = False
                    message = await await_event(
                        client,
                        events.MessageEdited(FATHER),
                        button.click()
                    )


def get_bot_id(button):
    return int(button.data[button.data.index(b'/') + 1:])


async def load_bots(client):
    bots = []
    async with aclosing(iter_buttons(client)) as it:
        async for button in it:
            bots.append((get_bot_id(button), button.text))

    return bots


async def get_bot_menu(client, bot_id, subpart=None):
    async with aclosing(iter_buttons(client)) as it:
        async for button in it:
            if get_bot_id(button) == bot_id:
                message = await await_event(
                    client,
                    events.MessageEdited(FATHER),
                    pre=button.click()
                )
                if subpart:
                    message = await await_event(
                        client,
                        events.MessageEdited,
                        pre=message.click(data=button.data + b'/' + subpart)
                    )

                return message
        else:
            eprint('No bot with ID', bot_id, 'found')


async def get_token(client, bot_id, revoke=True):
    path = 'bots/{}/tokn'.format(bot_id).encode('ascii')
    message = await get_bot_menu(client, bot_id)
    message = await await_event(
        client,
        events.MessageEdited(FATHER),
        pre=message.click(data=path)
    )
    if revoke:
        message = await await_event(
            client,
            events.MessageEdited(FATHER),
            pre=message.click(data=path + b'/revoke')
        )

    for entity, text in message.get_entities_text():
        if isinstance(entity, types.MessageEntityCode):
            return text

    eprint('Failed to retrieve token for bot', bot_id)


async def delete_bot(client, config, bot_id):
    path = 'bots/{}/del'.format(bot_id).encode('ascii')
    message = await get_bot_menu(client, bot_id)
    for _ in range(3):
        message = await await_event(
            client,
            events.MessageEdited(FATHER),
            pre=message.click(data=path)
        )
        path += b'/yes'

    for i, t in enumerate(config.bots):
        if t[0] == bot_id:
            del config.bots[i]
            config.save()
            break


async def create_bot(client, config, name):
    if '@' not in name:
        eprint('You must specify your bot name as "Bot Name@username"')

    name, username = name.rsplit('@', 1)
    name = name.strip()
    username = username.strip()
    if username[-3:].lower() != 'bot':
        username += 'bot'

    message = await await_event(
        client,
        events.NewMessage(FATHER),
        client.send_message(FATHER, '/newbot')
    )
    if message.raw_text.startswith(MAX_BOTS_MESSAGE):
        eprint('You must delete older bots before creating a new one')

    await await_event(
        client,
        events.NewMessage(FATHER),
        client.send_message(FATHER, name)
    )
    message = await await_event(
        client,
        events.NewMessage(FATHER),
        client.send_message(FATHER, username)
    )

    for entity, text in message.get_entities_text():
        if isinstance(entity, types.MessageEntityCode):
            bot_id = await client.get_peer_id(username)
            config.bots.insert(0, (bot_id, username))
            config.save()
            return text

    eprint('Bot created but failed to retrieve token')


async def edit(client, bot_id, name, action):
    message = await get_bot_menu(client, bot_id, b'edit')
    path = 'bots/{}/edit/{}'.format(bot_id, name).encode('ascii')
    await await_event(
        client,
        events.MessageEdited(FATHER),
        pre=message.click(data=path)
    )
    await action


async def edit_commands(client, bot_id, commands):
    for i, cmd in enumerate(commands):
        cmd = cmd.split('-', 1)
        l, r = cmd if len(cmd) == 2 else (cmd, None)
        commands[i] = '{} - {}'.format(l, r or '(no description)')

    await edit(client, bot_id, 'comm',
               client.send_message(FATHER, '\n'.join(commands)))


async def main():
    config = Config()
    parser = argparse.ArgumentParser()

    no_bot = parser.add_argument_group('general actions').add_argument
    need_bot = parser.add_argument_group('actions on a bot').add_argument
    no_bot('-a', '--api', nargs=2, metavar=('API_ID', 'API_HASH'),
           help='sets the API ID/API hash pair')

    no_bot('-c', '--create', nargs=2, metavar=('USERNAME', 'DISPLAY'),
           help='creates a bot with the given display name')

    no_bot('-r', '--reload', action='store_true',
           help='reloads the list of bots')

    no_bot('-l', '--list', action='store_true',
           help='lists owned bots (reloads if no bots are known)')

    need_bot('-g', '--gentoken', metavar='BOT',
             help='generate and get a new token for a bot')

    need_bot('-t', '--token', metavar='BOT',
             help='get an existing token for a bot')

    need_bot('-d', '--delete', metavar='BOT',
             help='deletes a bot')

    need_bot('-n', '--name', nargs=2, metavar=('BOT', 'NAME'),
             help='set a new name for a bot')

    need_bot('-i', '--info', nargs=2, metavar=('BOT', 'INFO'),
             help='set the info for the bot shown in the chat')

    need_bot('-b', '--bio', nargs=2, metavar=('BOT', 'BIO'),
             help='set the biography (about) for the bot')

    need_bot('-p', '--photo', nargs=2, metavar=('BOT', 'PHOTO'),
             help='set the profile photo for a bot')

    need_bot('-m', '--commands', nargs='+', metavar=('BOT', 'CMD-DESC'),
             help='set the commands with their description for the bot')

    need_bot('-e', '--inline', nargs=2, metavar=('BOT', 'HINT'),
             help='set the inline placeholder hint')

    args = parser.parse_args()
    if not config.api_id and not args.api:
        eprint('Please configure API ID and hash by running with '
               '--api 12345:1a2b3c4d5e6f')
    elif args.api:
        api_id, api_hash = args.api
        config.api_id = int(api_id)
        config.api_hash = api_hash

    async with TelegramClient(
            config.session_name, config.api_id, config.api_hash) as client:
        if args.reload or (args.list and not config.bots):
            config.bots = await load_bots(client)

        if args.list:
            pad = max(len(t[1]) for t in config.bots)
            for bot_id, bot_username in config.bots:
                print('{:<{pad}} ID:{}'
                      .format(bot_username, bot_id, pad=pad))

            print('Total: ', len(config.bots))

        if args.create:
            print(await create_bot(client, config, args.create))

        if args.token or args.gentoken:
            bot_id = find_bot(config, args.token or args.gentoken)
            print(await get_token(client, bot_id, revoke=bool(args.gentoken)))

        if args.delete:
            bot_id = find_bot(config, args.delete)
            await delete_bot(client, config, bot_id)

        if args.name:
            bot_id = find_bot(config, args.name[0])
            await edit(client, bot_id, 'name',
                       client.send_message(FATHER, args.name[1]))

        if args.info:
            bot_id = find_bot(config, args.info[0])
            await edit(client, bot_id, 'desc',
                       client.send_message(FATHER, args.info[1]))

        if args.bio:
            bot_id = find_bot(config, args.bio[0])
            await edit(client, bot_id, 'desc',
                       client.send_message(FATHER, args.bio[1]))

        if args.photo:
            bot_id = find_bot(config, args.photo[0])
            await edit(client, bot_id, 'pic',
                       client.send_file(FATHER, args.photo[1]))

        if args.commands:
            commands = args.commands[1:]
            if commands:
                bot_id = find_bot(config, args.commands[0])
                await edit_commands(client, bot_id, commands)

        if args.inline:
            bot_id = find_bot(config, args.inline[0])
            await edit(client, bot_id, 'inph',
                       client.send_file(FATHER, args.inline[1]))


if __name__ == '__main__':
    asyncio.get_event_loop().run_until_complete(main())
