import discord
import os
import json
from dotenv import load_dotenv
from discord.ext import commands
from datetime import timedelta

permissoes = discord.Intents.default() # permissões do bot
permissoes.message_content = True
status = discord.Activity(name = "Use !helpme para ver os comandos. ", type = discord.ActivityType.watching)
bot = commands.Bot(command_prefix ='!', intents = permissoes, activity = status)

# MIN = 5
# MAX = 9

opcoes_match_final = {}


# Comando de Instruções
@bot.command()
async def helpme(ctx):
    await ctx.send(f"Caso queira que gerar uma enquete, basta digitar o comando '!rebola' seguido de espaço e as partidas.")
    await ctx.send("Para adicionar um novo jogador, use o comando '!linkar' seguido do apelido cadastrado no site do X5.")
    await ctx.send("Para a Auditoria, o comando é '!auditar'. O Bot irá mostrar quantos votos houveram, se a auditoria foi aprovada ou reprovada e quem votou nas partidas.")

# Função para pegar o nome do usuário Discord e cadastrá-lo no JSON
@bot.command()
async def linkar(ctx, nickname):
    try:
        with open('links.json', 'r') as f:
            links = json.load(f)
    except FileNotFoundError:
        links = {} 
    
    # Adicionando o id do usuário com seu nickname ao dicionário

    id_usuario = str(ctx.author.id)
    links[id_usuario] = nickname
    
    with open('links.json', 'w') as f:
        json.dump(links, f, indent=4)
    
    # Envio da confirmação para o usuário
    await ctx.send(f"Seu nick {nickname} foi vinculado com sucesso.")


MATCH_EN = 'Match'
MATCH_PT = 'Partida'
SCORE_EN = 'Score'
SCORE_PT = 'Pontuação'

@bot.command()
async def rebola(ctx, *, opcoes):
    partidas_da_rodada = {} # Dicionário para validação
    n_match = 1
    options = []
    question = "Qual será a match?"

    # Define os divisores padrão
    divisor_partidas = MATCH_EN
    divisor_pontuacao = SCORE_EN

    # Verifica se a linguagem é Português
    if MATCH_PT in opcoes:
        divisor_partidas = MATCH_PT
        divisor_pontuacao = SCORE_PT

    # Divide o bloco total pelas partidas (ex: Partida 1, Partida 2...)
    blocos_opcoes = opcoes.split(divisor_partidas)
    
    for bloco in blocos_opcoes[1:]:
        linhas = bloco.split('\n')
        time_dir = []
        time_esq = []
        
        for linha in linhas: 
            linha = linha.strip()
            
            # Filtro Robusto:
            # 1. Deve ter 'x' (separador de times)
            # 2. Não deve ter o divisor de pontuação
            # 3. Deve conter parênteses '(' (indicativo de que é um jogador com pontos/elo)
            # Isso ignora links como https://x.com/... ou links no fim da lista
            if ('x' in linha) and (divisor_pontuacao not in linha) and ('(' in linha):
                try:
                    match = linha.split('x') 
                    if len(match) < 2:
                        continue

                    # --- Processamento Jogador Esquerdo ---
                    # Formato esperado: 'Top: Vinicim (8) '
                    jogador_esq_format = match[0] 
                    if ":" in jogador_esq_format:
                        # Divide no ':' e pega o que vem depois (o nome e ponto)
                        partes_esq = jogador_esq_format.split(":")
                        # ' Vinicim (8) ' -> split('(') -> [' Vinicim ', '8) ']
                        nome_esq_bruto = partes_esq[1].split("(")[0]
                        time_esq.append(nome_esq_bruto.strip())

                    # --- Processamento Jogador Direito ---
                    # Formato esperado: ' (8) Pedro Ruim'
                    jogador_dir_format = match[1] 
                    if ")" in jogador_dir_format:
                        # ' (8) Pedro Ruim' -> split(')') -> [' (8', ' Pedro Ruim']
                        partes_dir = jogador_dir_format.split(")")
                        if len(partes_dir) > 1:
                            nome_dir_final = partes_dir[1].strip()
                            time_dir.append(nome_dir_final)
                            
                except Exception as e:
                    print(f"Erro ao processar linha: {linha}. Erro: {e}")
                    continue # Pula a linha se estiver mal formatada
            else:
                continue 
        
        # Só registra a partida se conseguiu extrair jogadores para ambos os lados
        if time_esq and time_dir:
            chave_partida = f"Match {n_match}"
            partidas_da_rodada[chave_partida] = {
                'esquerdo': time_esq,
                'direito': time_dir
            }
            options.append(chave_partida)
            n_match += 1

    # Se nenhuma partida válida foi encontrada, avisa o usuário
    if not options:
        await ctx.send("Não consegui encontrar partidas válidas no texto enviado.")
        return

    # Salva os dados processados no JSON
    with open('partidas.json', 'w', encoding='utf-8') as f:
        json.dump(partidas_da_rodada, f, indent=4, ensure_ascii=False)

    # Adiciona a opção extra da enquete
    options.append("Rebola")

    # Criação da Enquete (Discord.py 2.4+)
    my_poll = discord.Poll(
        question=discord.PollMedia(text=question),
        duration=timedelta(hours=1)
    )

    # Adiciona as opções de "Match X" na enquete
    for option in options:
        my_poll.add_answer(text=option)

    # Envia a enquete
    await ctx.send(poll=my_poll)


@bot.command()
async def auditar(ctx):

    # Lendo os arquivos JSON com tratamento de erro para caso o arquivo não seja encontrado.

    try:
        with open('links.json', 'r') as f:
            jogadores = json.load(f)
    except FileNotFoundError: 
        jogadores = {}

    try:
        with open('partidas.json', 'r') as f:
            partidas = json.load(f)
    except FileNotFoundError:
        partidas = {}
    
    enquete_encontrada = None
    
    # Busca as últimas 50 mensagens enviadas no canal
    async for message in ctx.channel.history(limit=50):

        # Caso o autor da mensagem seja o bot e ela seja uma enquete, buscamos os dados dela.
        if message.author == bot.user and message.poll: 
            enquete_encontrada = message.poll
            break # Encerra o bloco caso a condição seja verdadeira.

    # Caso não haja uma mensagem do tipo enquete, retorna uma resposta de aviso.
    if enquete_encontrada is None:
        await ctx.send("Não encontrei nenhuma enquete recente neste canal.")
        return # Encerra a função.
    
        
    await ctx.send("Enquete encontrada! Iniciando auditoria...") # Mensagem para avisar que a auditoria começou.

    maior_contagem = 0 # Definindo a variável para guardar o número de votos  
    opcao_vencedora = '' # Definindo a variável que irá guardar a opção vencedora

    # Para cada uma das opções da enquete...
    for opcao in enquete_encontrada.answers:
        # Caso a contagem de votos na opção seja maior do que a maior contagem armazenada...
        if opcao.vote_count > maior_contagem:

            maior_contagem = opcao.vote_count # Armazeno a quantidade de votos da opção mais votada
            opcao_vencedora = opcao.text # Armazeno a opção com mais votos

    if opcao_vencedora == "Rebola":
        await ctx.send("Auditoria não aplicável na opção 'Rebola'.")
        return
    
    elif maior_contagem == 0:
        await ctx.send("Não houveram votos suficientes para iniciar a auditoria.")
        return
    
    else:
        await ctx.send(f"Partida vencedora: {opcao_vencedora}.")
        await ctx.send(f"Total Votos: {maior_contagem}\n\n")
        await ctx.send(f"Iniciando a auditoria...")

    # Iniciando a auditoria...

    votantes_da_opcao_vencedora = []

    for opcao in enquete_encontrada.answers:
        if opcao.text == opcao_vencedora:
            votantes_da_opcao_vencedora = [member async for member in opcao.voters()]
            break
    
        
    times_da_partida_vencedora = partidas[opcao_vencedora]

    time_esquerdo = times_da_partida_vencedora['esquerdo']

    time_direito = times_da_partida_vencedora['direito']


    votos_time_esq = 0
    votantes_time_esq = []
    
    votos_time_dir = 0
    votantes_time_dir = []

    for votante in votantes_da_opcao_vencedora:

        id_votante = str(votante.id)

        nickname = jogadores[id_votante]

        # Se o ID do jogador estiver no time esquerdo:
        if nickname in time_esquerdo:
            votos_time_esq += 1
            votantes_time_esq.append(nickname)

        # Se o ID do jogador estiver no time direito:
        elif nickname in time_direito:
            votos_time_dir += 1
            votantes_time_dir.append(nickname)
    
    votantes_time_esq_j = ", ".join(votantes_time_esq)
    votantes_time_dir_j = ",".join(votantes_time_dir)
    
    mensagem_final = f"Resultado da auditoria para {opcao_vencedora}: \n"
    mensagem_final += f"Votos do Time Esquerdo: {votos_time_esq}\n "
    mensagem_final += f"Pessoas que votaram no Time Esquerdo: {votantes_time_esq_j}\n\n"
    mensagem_final += f"Votos do Time Direito: {votos_time_dir}\n"
    mensagem_final += f"Pessoas que votaram no Time Direito: {votantes_time_dir_j}\n\n"

    if votos_time_dir >= 2 and votos_time_esq >= 2:
        mensagem_final += f"**Auditoria Aprovada.**"
    else:
        mensagem_final += f"**Auditoria Reprovada.**"
    
    await ctx.send(mensagem_final)
    
          

# # Para o voto normal, o código é o descrito abaixo
# @bot.command()
# async def rebola(ctx, num=MIN):
#     # Inicio perguntando qual será a opção escolhida
#     question = "Qual será a match?"
#     options = []

#     if num < MIN or num > MAX:
#         await ctx.send(f"Você pode escolher entre {MIN} e {MAX} opções. Digite o comando novamente.")
#     else:
#         for n in range(1, num+1):
#             options.append(f"Match {n}")
#         options.append("Rebola")

#         my_poll = discord.Poll(
#         question=discord.PollMedia(text=question),
#         duration=timedelta(hours=1)
#         )

#         for option_text in options:
#             my_poll.add_answer(text=option_text)

#         # Envia a enquete usando o argumento 'poll'
#         await ctx.send(poll=my_poll)   
        

@bot.event
async def on_ready():
    print("Estou pronto!")

@bot.event
async def on_command_error(ctx, error):
  if isinstance(error, commands.BadArgument):
    await ctx.send("O valor informado deve ser um número inteiro (Ex: 7). Digite o comando novamente.")

load_dotenv() # Carrega as variáveis do arquivo .env

TOKEN = os.getenv('DISCORD_TOKEN')

bot.run(TOKEN)
