import asyncio
import json
import logging
import os
import re
import sys
from datetime import timedelta

import discord
from discord.ext import commands
from dotenv import load_dotenv

LINKS_FILE = "links.json"
PARTIDAS_FILE = "partidas.json"
MAX_STORED_POLLS = 50
MIN_VOTOS_POR_TIME = 2

MATCH_EN = "Match"
MATCH_PT = "Partida"
SCORE_EN = "Score"
SCORE_PT = "Pontuação"
SPLIT_X = re.compile(r"\s+x\s+", re.IGNORECASE)

COLOR_INFO = 0x5865F2
COLOR_OK = 0x57F287
COLOR_FAIL = 0xED4245
COLOR_WARN = 0xFEE75C

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("bot-rebola")

permissoes = discord.Intents.default()
permissoes.message_content = True
status = discord.Activity(
    name="Use !helpme ou /helpme",
    type=discord.ActivityType.watching,
)
bot = commands.Bot(command_prefix="!", intents=permissoes, activity=status)
_lock_file = None


def acquire_process_lock():
    """Impede duas instâncias locais do bot de usarem o mesmo diretório."""
    global _lock_file
    lock_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bot.lock")
    _lock_file = open(lock_path, "a+b")
    _lock_file.seek(0)
    if _lock_file.read(1) == b"":
        _lock_file.write(b"0")
        _lock_file.flush()
    _lock_file.seek(0)

    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(_lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(_lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        _lock_file.close()
        _lock_file = None
        raise SystemExit(
            "O Bot Rebola já está rodando neste diretório. "
            "Encerre a outra instância antes de iniciar uma nova."
        )


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        log.exception("JSON inválido em %s", path)
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def load_links():
    data = load_json(LINKS_FILE, {})
    return data if isinstance(data, dict) else {}


def save_links(links):
    save_json(LINKS_FILE, links)


def load_partidas_store():
    data = load_json(PARTIDAS_FILE, {})
    if not isinstance(data, dict) or not data:
        return {}
    first = next(iter(data.values()), None)
    if isinstance(first, dict) and "esquerdo" in first:
        return {}
    return data


def save_poll_roster(poll_message_id, channel_id, partidas, companion_message_id=None):
    store = load_partidas_store()
    key = str(poll_message_id)
    if key in store:
        store.pop(key)
    store[key] = {
        "channel_id": str(channel_id),
        "partidas": partidas,
        "companion_message_id": (
            str(companion_message_id) if companion_message_id is not None else None
        ),
    }
    while len(store) > MAX_STORED_POLLS:
        store.pop(next(iter(store)))
    save_json(PARTIDAS_FILE, store)


def get_poll_roster(poll_message_id):
    entry = get_poll_entry(poll_message_id)
    if not entry or not isinstance(entry, dict):
        return None
    partidas = entry.get("partidas")
    return partidas if isinstance(partidas, dict) else None


def get_poll_entry(poll_message_id):
    return load_partidas_store().get(str(poll_message_id))


def nick_key(value):
    return value.strip().casefold()


def parse_partidas(opcoes):
    partidas_da_rodada = {}
    n_match = 1

    divisor_partidas = MATCH_EN
    divisor_pontuacao = SCORE_EN
    if MATCH_PT in opcoes:
        divisor_partidas = MATCH_PT
        divisor_pontuacao = SCORE_PT

    for bloco in opcoes.split(divisor_partidas)[1:]:
        time_dir = []
        time_esq = []

        for linha in bloco.split("\n"):
            linha = linha.strip()
            if ("x" not in linha.lower()) or (divisor_pontuacao in linha) or ("(" not in linha):
                continue
            if SPLIT_X.search(linha) is None:
                continue

            try:
                match = SPLIT_X.split(linha, maxsplit=1)
                if len(match) < 2:
                    continue

                jogador_esq_format = match[0]
                if ":" in jogador_esq_format:
                    partes_esq = jogador_esq_format.split(":")
                    nome_esq_bruto = partes_esq[1].split("(")[0]
                    nome_esq = nome_esq_bruto.strip()
                    if nome_esq:
                        time_esq.append(nome_esq)

                jogador_dir_format = match[1]
                if ")" in jogador_dir_format:
                    partes_dir = jogador_dir_format.split(")")
                    if len(partes_dir) > 1:
                        nome_dir_final = partes_dir[1].strip()
                        if nome_dir_final:
                            time_dir.append(nome_dir_final)
            except Exception:
                log.exception("Erro ao processar linha: %s", linha)
                continue

        if time_esq and time_dir:
            chave_partida = f"Match {n_match}"
            partidas_da_rodada[chave_partida] = {
                "esquerdo": time_esq,
                "direito": time_dir,
            }
            n_match += 1

    return partidas_da_rodada


def list_or_none(items):
    return ", ".join(items) if items else "Ninguém"


def roster_players(partidas):
    players = []
    seen = set()
    for times in partidas.values():
        for side in ("esquerdo", "direito"):
            for nickname in times.get(side, []):
                key = nick_key(nickname)
                if key not in seen:
                    seen.add(key)
                    players.append(nickname)
    return players


def pending_voters_embed(partidas, voted_nicks=None):
    voted_nicks = voted_nicks or set()
    pending = [
        nickname
        for nickname in roster_players(partidas)
        if nick_key(nickname) not in voted_nicks
    ]
    if pending:
        description = "\n".join(f"• {nickname}" for nickname in pending)
    else:
        description = "Todos os jogadores vinculados da rodada já votaram."
    return discord.Embed(
        title=f"Ainda não votaram ({len(pending)})",
        description=description,
        color=COLOR_WARN if pending else COLOR_OK,
    )


def help_embed():
    embed = discord.Embed(
        title="Comandos do Bot Rebola",
        description="Gere enquetes a partir do paste do X5 e audite os votos das partidas.",
        color=COLOR_INFO,
    )
    embed.add_field(
        name="!rebola / /rebola",
        value="Cole as partidas do X5 depois do comando para gerar a enquete.",
        inline=False,
    )
    embed.add_field(
        name="!linkar / /linkar",
        value="Vincule seu apelido cadastrado no site do X5. Ex: `!linkar Pedro Ruim`",
        inline=False,
    )
    embed.add_field(
        name="!deslinkar / /deslinkar",
        value="Remove o vínculo do seu usuário Discord.",
        inline=False,
    )
    embed.add_field(
        name="!links / /links",
        value="Lista os apelidos já vinculados.",
        inline=False,
    )
    embed.add_field(
        name="!auditar / /auditar",
        value=(
            "Audita a enquete mais recente do canal, ou a enquete marcada na resposta. "
            "Também dá para usar o botão **Auditar** na mensagem das equipes."
        ),
        inline=False,
    )
    return embed


def link_success_embed(nickname, trocou=False):
    titulo = "Apelido atualizado" if trocou else "Apelido vinculado"
    return discord.Embed(
        title=titulo,
        description=f"Seu nick **{nickname}** foi vinculado com sucesso.",
        color=COLOR_OK,
    )


def links_embed(links):
    if not links:
        return discord.Embed(
            title="Jogadores vinculados",
            description="Ninguém vinculou um apelido ainda. Use `!linkar` ou `/linkar`.",
            color=COLOR_WARN,
        )

    linhas = [
        f"<@{user_id}> → **{nick}**"
        for user_id, nick in sorted(links.items(), key=lambda item: nick_key(str(item[1])))
    ]
    texto = "\n".join(linhas)
    if len(texto) > 4000:
        texto = texto[:3990] + "\n..."
    return discord.Embed(title="Jogadores vinculados", description=texto, color=COLOR_INFO)


def simple_embed(title, description, color=COLOR_WARN):
    return discord.Embed(title=title, description=description, color=color)


def attach_poll_link(embed, poll_message):
    embed.add_field(
        name="Enquete",
        value=f"[Ir para a enquete]({poll_message.jump_url})",
        inline=False,
    )
    return embed


async def fetch_poll_message(channel, message_id):
    try:
        message = await channel.fetch_message(int(message_id))
    except (discord.NotFound, discord.HTTPException, TypeError, ValueError):
        return None
    return message if message.poll else None


async def resolve_poll_from_message(channel, message):
    if message is None:
        return None
    if message.poll:
        return message
    if bot.user and message.author.id == bot.user.id and message.reference:
        parent = message.reference.resolved
        if parent is None and message.reference.message_id:
            try:
                parent = await channel.fetch_message(message.reference.message_id)
            except discord.HTTPException:
                return None
        if parent and parent.poll:
            return parent
    return None


async def find_latest_poll(channel):
    async for message in channel.history(limit=50):
        if bot.user and message.author.id == bot.user.id and message.poll:
            return message
    return None


async def resolve_command_poll(ctx):
    if ctx.message and ctx.message.reference:
        ref = ctx.message.reference
        referenced = ref.resolved if isinstance(ref.resolved, discord.Message) else None
        if referenced is None and ref.message_id:
            try:
                referenced = await ctx.channel.fetch_message(ref.message_id)
            except discord.HTTPException:
                referenced = None
        found = await resolve_poll_from_message(ctx.channel, referenced)
        if found:
            return found
    return await find_latest_poll(ctx.channel)


async def collect_voters(answer):
    try:
        return [member async for member in answer.voters()]
    except discord.HTTPException:
        log.exception("Não foi possível listar votantes da opção %s", answer.text)
        return None


async def refresh_pending_voters(poll_message):
    entry = get_poll_entry(poll_message.id)
    if not entry:
        return

    companion_id = entry.get("companion_message_id")
    partidas = entry.get("partidas")
    if not companion_id or not isinstance(partidas, dict):
        return

    try:
        poll_message = await poll_message.channel.fetch_message(poll_message.id)
    except discord.HTTPException:
        return
    if poll_message.poll is None:
        return

    links = load_links()
    voted_nicks = set()
    for answer in poll_message.poll.answers:
        voters = await collect_voters(answer)
        if voters is None:
            return
        for voter in voters:
            nickname = links.get(str(voter.id))
            if nickname:
                voted_nicks.add(nick_key(nickname))

    try:
        companion = await poll_message.channel.fetch_message(int(companion_id))
        await companion.edit(embed=pending_voters_embed(partidas, voted_nicks))
    except (discord.NotFound, discord.HTTPException, TypeError, ValueError):
        log.exception("Não foi possível atualizar a lista de quem ainda não votou")


async def perform_audit(poll_message):
    try:
        poll_message = await poll_message.channel.fetch_message(poll_message.id)
    except discord.HTTPException:
        return simple_embed("Auditoria", "Não consegui atualizar os dados dessa enquete.")

    poll = poll_message.poll
    if poll is None:
        return simple_embed("Auditoria", "Essa mensagem não é uma enquete.")

    attach_poll_link_later = poll_message
    contagens = []
    maior_contagem = 0
    for opcao in poll.answers:
        votos = opcao.vote_count or 0
        contagens.append((opcao, votos))
        if votos > maior_contagem:
            maior_contagem = votos

    if maior_contagem == 0:
        return attach_poll_link(
            simple_embed("Auditoria", "Não houve votos suficientes para iniciar a auditoria."),
            attach_poll_link_later,
        )

    vencedoras = [opcao for opcao, votos in contagens if votos == maior_contagem]
    if len(vencedoras) > 1:
        nomes = ", ".join(opcao.text for opcao in vencedoras)
        return attach_poll_link(
            simple_embed(
                "Empate na enquete",
                f"As opções **{nomes}** empataram com {maior_contagem} voto(s). "
                "A auditoria só roda quando houver uma opção vencedora.",
            ),
            attach_poll_link_later,
        )

    opcao_vencedora = vencedoras[0]
    if opcao_vencedora.text == "Rebola":
        return attach_poll_link(
            simple_embed(
                "Auditoria não aplicável",
                "A opção vencedora foi **Rebola**.",
            ),
            attach_poll_link_later,
        )

    partidas = get_poll_roster(poll_message.id)
    if not partidas or opcao_vencedora.text not in partidas:
        return attach_poll_link(
            simple_embed(
                "Roster não encontrado",
                "Não achei as equipes salvas para essa enquete. "
                "Gere a enquete de novo com `!rebola` ou `/rebola`.",
            ),
            attach_poll_link_later,
        )

    times = partidas[opcao_vencedora.text]
    time_esquerdo = times.get("esquerdo", [])
    time_direito = times.get("direito", [])
    mapa_esq = {nick_key(nome): nome for nome in time_esquerdo}
    mapa_dir = {nick_key(nome): nome for nome in time_direito}

    votantes = await collect_voters(opcao_vencedora)
    if votantes is None:
        return attach_poll_link(
            simple_embed(
                "Auditoria",
                f"A opção **{opcao_vencedora.text}** tem {maior_contagem} voto(s), "
                "mas não consegui listar quem votou.",
            ),
            attach_poll_link_later,
        )

    links = load_links()
    votantes_esq = []
    votantes_dir = []
    outros = []
    nicks_que_votaram = set()

    for votante in votantes:
        nickname = links.get(str(votante.id))
        if not nickname:
            outros.append(f"{votante.mention} (não vinculado)")
            continue

        chave = nick_key(nickname)
        if chave in mapa_esq:
            votantes_esq.append(nickname)
            nicks_que_votaram.add(chave)
        elif chave in mapa_dir:
            votantes_dir.append(nickname)
            nicks_que_votaram.add(chave)
        else:
            outros.append(f"{votante.mention} → **{nickname}** (fora da partida)")

    votos_esq = len(votantes_esq)
    votos_dir = len(votantes_dir)
    aprovada = votos_esq >= MIN_VOTOS_POR_TIME and votos_dir >= MIN_VOTOS_POR_TIME

    nao_votaram_esq = [nome for nome in time_esquerdo if nick_key(nome) not in nicks_que_votaram]
    nao_votaram_dir = [nome for nome in time_direito if nick_key(nome) not in nicks_que_votaram]

    embed = discord.Embed(
        title="Auditoria Aprovada" if aprovada else "Auditoria Reprovada",
        description=(
            f"Partida vencedora: **{opcao_vencedora.text}**\n"
            f"Total de votos na opção: **{maior_contagem}**"
        ),
        color=COLOR_OK if aprovada else COLOR_FAIL,
    )
    embed.add_field(
        name=f"Time Esquerdo ({votos_esq})",
        value=list_or_none(votantes_esq),
        inline=False,
    )
    embed.add_field(
        name=f"Time Direito ({votos_dir})",
        value=list_or_none(votantes_dir),
        inline=False,
    )
    embed.add_field(
        name="Outros votos",
        value=list_or_none(outros),
        inline=False,
    )
    embed.add_field(
        name="Não votaram na vencedora",
        value=(
            f"**Esquerdo:** {list_or_none(nao_votaram_esq)}\n"
            f"**Direito:** {list_or_none(nao_votaram_dir)}"
        ),
        inline=False,
    )
    return attach_poll_link(embed, attach_poll_link_later)


async def send_audit_result(embed, poll_message, ctx=None, interaction=None):
    if interaction is not None:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed)
        else:
            await interaction.response.send_message(embed=embed)
        return

    if ctx is not None:
        await ctx.send(embed=embed)
        return

    await poll_message.channel.send(embed=embed)


class AuditButton(discord.ui.DynamicItem[discord.ui.Button], template=r"auditar:(?P<poll_id>\d+)"):
    def __init__(self, poll_id: int):
        self.poll_id = poll_id
        super().__init__(
            discord.ui.Button(
                label="Auditar",
                style=discord.ButtonStyle.primary,
                custom_id=f"auditar:{poll_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["poll_id"]))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        channel = interaction.channel
        if channel is None:
            await interaction.followup.send("Não consegui acessar o canal.", ephemeral=True)
            return

        poll_message = await fetch_poll_message(channel, self.poll_id)
        if poll_message is None:
            await interaction.followup.send(
                "Não encontrei a enquete ligada a este botão.",
                ephemeral=True,
            )
            return

        embed = await perform_audit(poll_message)
        await refresh_pending_voters(poll_message)
        await send_audit_result(embed, poll_message, interaction=interaction)


def make_audit_view(poll_id):
    view = discord.ui.View(timeout=None)
    view.add_item(AuditButton(poll_id))
    return view


class RelinkConfirmView(discord.ui.View):
    def __init__(self, user_id: int, new_nick: str, old_nick: str):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.new_nick = new_nick
        self.old_nick = old_nick
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Só quem usou o comando pode confirmar.",
                ephemeral=True,
            )
            return False
        return True

    async def _disable(self, interaction, embed=None, content=None):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content=content, embed=embed, view=self)
        self.stop()

    @discord.ui.button(label="Confirmar", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        links = load_links()
        links[str(self.user_id)] = self.new_nick
        save_links(links)
        await self._disable(interaction, embed=link_success_embed(self.new_nick, trocou=True))

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._disable(
            interaction,
            embed=simple_embed(
                "Vinculação cancelada",
                f"Seu apelido continua **{self.old_nick}**.",
                COLOR_INFO,
            ),
        )

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


bot.add_dynamic_items(AuditButton)


@bot.hybrid_command(name="helpme", description="Mostra os comandos do bot")
async def helpme(ctx):
    await ctx.send(embed=help_embed())


@bot.hybrid_command(name="linkar", description="Vincula seu apelido do X5")
async def linkar(ctx, *, nickname: str):
    nickname = nickname.strip()
    if not nickname:
        await ctx.send(
            embed=simple_embed("Apelido inválido", "Informe o apelido cadastrado no site do X5."),
            ephemeral=True,
        )
        return

    links = load_links()
    user_id = str(ctx.author.id)
    atual = links.get(user_id)

    if atual and nick_key(atual) == nick_key(nickname):
        await ctx.send(embed=link_success_embed(nickname))
        return

    if atual:
        view = RelinkConfirmView(ctx.author.id, nickname, atual)
        mensagem = await ctx.send(
            embed=simple_embed(
                "Trocar apelido?",
                f"Você já está vinculado a **{atual}**. Trocar para **{nickname}**?",
                COLOR_WARN,
            ),
            view=view,
        )
        view.message = mensagem
        return

    links[user_id] = nickname
    save_links(links)
    await ctx.send(embed=link_success_embed(nickname))


@bot.hybrid_command(name="deslinkar", description="Remove o vínculo do seu apelido do X5")
async def deslinkar(ctx):
    links = load_links()
    user_id = str(ctx.author.id)
    removido = links.pop(user_id, None)
    if removido is None:
        await ctx.send(
            embed=simple_embed(
                "Nada para remover",
                "Você ainda não tem um apelido vinculado.",
            )
        )
        return
    save_links(links)
    await ctx.send(
        embed=simple_embed(
            "Vínculo removido",
            f"O apelido **{removido}** não está mais ligado à sua conta.",
            COLOR_OK,
        )
    )


@bot.hybrid_command(name="links", description="Lista os apelidos vinculados")
async def listar_links(ctx):
    await ctx.send(embed=links_embed(load_links()))


async def create_rebola_messages(send, channel_id, opcoes):
    partidas_da_rodada = parse_partidas(opcoes)
    if not partidas_da_rodada:
        await send(
            embed=simple_embed(
                "Nenhuma partida encontrada",
                "Não consegui encontrar partidas válidas no texto enviado.",
            )
        )
        return

    options = list(partidas_da_rodada.keys())
    options.append("Rebola")

    my_poll = discord.Poll(
        question=discord.PollMedia(text="Qual será a match?"),
        duration=timedelta(hours=1),
    )
    for option in options:
        my_poll.add_answer(text=option)

    poll_msg = await send(poll=my_poll)
    companion_msg = await send(
        embed=pending_voters_embed(partidas_da_rodada),
        view=make_audit_view(poll_msg.id),
    )
    save_poll_roster(
        poll_msg.id,
        channel_id,
        partidas_da_rodada,
        companion_message_id=companion_msg.id,
    )


class RebolaModal(discord.ui.Modal, title="Gerar enquete"):
    opcoes = discord.ui.TextInput(
        label="Partidas do X5",
        style=discord.TextStyle.paragraph,
        placeholder="Cole aqui o texto completo com Match 1, Match 2...",
        required=True,
        max_length=4000,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await create_rebola_messages(
            interaction.followup.send,
            interaction.channel_id,
            str(self.opcoes),
        )


@bot.command(name="rebola")
async def rebola(ctx, *, opcoes: str):
    await create_rebola_messages(ctx.send, ctx.channel.id, opcoes)


@bot.tree.command(name="rebola", description="Gera a enquete a partir do paste do X5")
async def rebola_slash(interaction: discord.Interaction):
    await interaction.response.send_modal(RebolaModal())


@bot.hybrid_command(name="auditar", description="Audita a enquete da rodada")
async def auditar(ctx):
    poll_message = await resolve_command_poll(ctx)
    if poll_message is None:
        await ctx.send(
            embed=simple_embed(
                "Enquete não encontrada",
                "Não encontrei nenhuma enquete recente neste canal. "
                "Responda a uma enquete com `!auditar` ou use o botão **Auditar**.",
            )
        )
        return

    if ctx.interaction:
        await ctx.defer()

    embed = await perform_audit(poll_message)
    await refresh_pending_voters(poll_message)
    await send_audit_result(embed, poll_message, ctx=ctx)


async def handle_poll_vote_change(payload):
    entry = get_poll_entry(payload.message_id)
    if not entry:
        return

    await asyncio.sleep(0.5)
    channel = bot.get_channel(payload.channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(payload.channel_id)
        except discord.HTTPException:
            return

    poll_message = await fetch_poll_message(channel, payload.message_id)
    if poll_message is not None:
        await refresh_pending_voters(poll_message)


@bot.event
async def on_raw_poll_vote_add(payload):
    await handle_poll_vote_change(payload)


@bot.event
async def on_raw_poll_vote_remove(payload):
    await handle_poll_vote_change(payload)


_synced = False


@bot.event
async def on_ready():
    global _synced
    if not _synced:
        try:
            await bot.tree.sync()
            for guild in bot.guilds:
                bot.tree.copy_global_to(guild=guild)
                await bot.tree.sync(guild=guild)
        except Exception:
            log.exception("Falha ao sincronizar os slash commands")
        _synced = True
    print("Estou pronto!", flush=True)


def _unwrap_command_error(error):
    if isinstance(error, commands.HybridCommandError):
        return _unwrap_command_error(error.original)
    if isinstance(error, commands.CommandInvokeError):
        return _unwrap_command_error(error.original)
    return error


@bot.event
async def on_command_error(ctx, error):
    error = _unwrap_command_error(error)

    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.MissingRequiredArgument):
        mensagens = {
            "linkar": "Informe o apelido do X5. Ex: `!linkar Pedro Ruim`",
            "rebola": "Cole as partidas depois do comando. Ex: `!rebola` + texto do X5",
        }
        texto = mensagens.get(ctx.command.name if ctx.command else "", "Faltou um argumento obrigatório.")
        await ctx.send(embed=simple_embed("Argumento obrigatório", texto))
        return

    log.exception("Erro no comando %s", ctx.command, exc_info=error)
    await ctx.send(embed=simple_embed("Erro", "Ocorreu um erro ao executar o comando.", COLOR_FAIL))


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    log.exception("Erro em slash command", exc_info=error)
    embed = simple_embed("Erro", "Ocorreu um erro ao executar o comando.", COLOR_FAIL)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.HTTPException:
        pass


def run_bot():
    acquire_process_lock()
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN não encontrado no arquivo .env")
    bot.run(token)


if __name__ == "__main__":
    run_bot()
