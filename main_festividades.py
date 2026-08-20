import pandas as pd
import numpy as np
from google import genai
from google.genai import types
from google.genai.errors import APIError

from enum import Enum
from pydantic import BaseModel, Field
from typing import List, Literal, Optional

import os
import time as time_module
import json
import glob
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor
from threading import Lock 
from tqdm import tqdm

from utils import salvar_sem_sobrescrever


# --- 1. CONFIGURAÇÕES INICIAIS ---
load_dotenv()
CHAVE_API = os.getenv("GEMINI_API_KEY")

client = genai.Client(api_key=CHAVE_API)

##Entrada: Dataset ordenado pelo valor no empenho (decrescente)
#CAMINHO_ENTRADA = "./bases/empenhos_juninos.xlsx"

##Entrada: Sample aleatória
#CAMINHO_ENTRADA = "./resultados/amostra_resultado.xlsx"

#Entrada: comparação com PE 
#CAMINHO_ENTRADA = "./bases/comparacao_pe.xlsx"

#Entrada: empenhos mais recentes
CAMINHO_ENTRADA = "./bases/empenhos_entre_3julho_e_04agosto_2026.csv"

#Saída:
CAMINHO_SAIDA_CSV = "./resultados/bruto_empenhos_classificados_entre_3julho_e_04agosto_2026.csv" 


COLUNA_HISTORICO = "Histórico"
COLUNA_ENTE = "Ente"

# NÚMERO MÁXIMO DE REQUISIÇÕES SIMULTÂNEAS
# Se for tier gratuito do Gemini (15 RPM), deixe em 2 ou 3. 
# Se for tier pago/Pay-as-you-go, pode subir para 15, 30 ou mais!
MAX_WORKERS = 25


# --- 2. O MODELO PYDANTIC DETALHADO ---
class ProcessoFestividadeFull(BaseModel):
        
    Festa: int = Field(
        description="""Analise o PROPÓSITO FINAL e a MOTIVAÇÃO PRINCIPAL do gasto descrito.
        - Responda 1 se o recurso público for destinado a viabilizar, estruturar, organizar ou realizar FESTIVIDADE PÚBLICA ou EVENTO COMEMORATIVO (ex: São João, padroeiros,
        emancipação, carnaval). Isso inclui: 
        a) Festividades públicas e comemorativas (ex: São João, padroeiros, carnaval, natal, reveillon, emancipação, etc).
        b) Eventos institucionais, corporativos, educacionais ou científicos (ex: conferências, palestras, feiras, exposições).
        c) Confraternizações e pequenas comemorações. (confraternizações de funcionários, inauguraçoes, premiações, comemoração do dia das mães, comemoração do dia das crianças, 
        festas setoriais, comemoração de conclusão de obra ou de alguma conquista pública, etc)
        d) Manifestações/eventos/apresentações/serviços culturais (orquestra sinfônica, banda filarmônica, apresentações folclóricas, apresentação de dança, capoeira, etc)
        Isso engloba TODA a cadeia do evento: desde a contratação da atração ou custos referente à ela, aluguel de equipamentos e estrutura temporária, etc, até adequações do 
        espaço físico, preparativos do local da festa e serviços de apoio direto ao evento. Pode incluir também gastos com espaço físico e aquisições permanentes para a 
        realização de festividades, como compra definitiva de equipamentos (caixas de som, iluminação, tendas próprias da prefeitura), desapropriação de terrenos para praças 
        de eventos, e construção ou reforma de espaços físicos destinados a festividades públicas
        - Responda 0 caso o gasto seja para a manutenção de rotinas administrativas, obras civis gerais, campanhas de saúde ou qualquer despesa que não tenha o objetivo explícito 
        e direto de realizar uma festa para o público.
        - Responda 0 se a despesa for em benefício de programas de assistência social (como Serviço de Convivência e Fortalecimento de Vínculos - SCFV e Programa e Serviços da
        Proteção Básica), mesmo se for referente a show, atração, decorações festivas ou comemorações em geral.
        """
    )

    Descricao_Despesa: Optional[str] = Field(
        default=None,
        description="""Se Festa=1, explique em no máximo 15 palavras o que EXATAMENTE está sendo pago, focando no objeto do empenho e não para quem ele se destina.
        Exemplo 1: "O empenho paga hospedagem e alimentação, e não o cachê da banda."
        Exemplo 2: "O gasto é com aluguel de palco, gerador e tendas."
        Exemplo 3: "Pagamento de um pacote de apresentações artísticas (música, teatro, circo)."
        Se Festa=0, retorne null."""
    )

    Tipos_Despesa: Optional[list[Literal["TD_1", "TD_2", "TD_3", "TD_4", "TD_5", "TD_6", "TD_7"]]] = Field(
        default=None, 
        description="""Se Festa = 1, classifique a natureza principal do gasto. Se Festa = 0, retorne null. Foque ESTRITAMENTE no serviço que está sendo pago (ex: se o texto diz
          "Hospedagem da Banda X", o gasto principal é a Hospedagem (TD_3), e NÃO a atração (TD_1)).
        TD_1 = Atração (Shows de músicos, cantores, bandas, pregadores/cantores religiosos, etc, de apelo comercial e alcance universal, contratados pelo porte para 
        entretenimento geral do público.)
        TD_2 = Estrutura (Itens estruturais que requerem montagem/instalação/engenharia: palco, sonorização, iluminação, gerador, banheiro químico, tendas, montagem, etc)
        TD_3 = Serviços de Apoio (Operações dinâmicas e de atendimento, focadas em suprir as necessidades humanas e manter o fluxo da festa em tempo real: hospedagem, alimentação, 
        buffet, bebidas, transporte, libras, segurança, brigadistas, bombeiros e proteção contra incêndios, ambulância e serviços médicos, locução, filmagem/fotografia, transmissão, cobertura midiática, etc)
        TD_4 = Pirotecnia (fogos de artifício, shows pirotécnicos)
        TD_5 = Cultural (Manifestações culturais, de tradição popular ou cívicas: quadrilhas juninas, maracatu, ala ursa, orquestras, tribos indígenas, trios de forró pé 
        de serra, grupos folclóricos, fanfarras, bandas de música municipais, banda filarmonica, grupos tradicionais de pífano, etc) Inclui despesas com contratação, figurinos, etc.
        TD_6 = Decoração/Ornamentação (balões, bandeiras, flores, luzes natalinas, estruturas ornamentais, etc)
        TD_7 = Outros (publicidade, organização, administração, aquisição de trofeus/medalhas/premios de premiações, aquisição de pequenos bens de consumo (descartáveis, 
        petiscos, guloseimas, pequenos lanches, ítens para camarins, etc), Infraestrutura Permanente e Aquisições (obras, adequações de terreno, aquisição definitiva de equipamento,
        etc)). Utilize apenas se não encaixar nas opções TD_1 a TD_6.

        DIFERENCIAÇÃO ENTRE TD_1 E TD_5: Avalie o PORTE do artista e o INTUITO da atração. Se for um show de massa contratado para entretenimento geral, é Atração (TD_1). 
        Se for um grupo contratado primordialmente pela sua representação das tradições e da cultura nordestina/local, é Cultural (TD_5).

        """
    )

    Tipo_Principal_Despesa: Optional[Literal["TD_1", "TD_2", "TD_3", "TD_4", "TD_5", "TD_6", "TD_7"]] = Field(
        default=None,
        description="""Se Festa = 1, escolha OBRIGATORIAMENTE apenas UM tipo dentre os listados em "Tipos_Despesa" que represente o objeto PRINCIPAL
          ou de MAIOR GASTO do empenho. Se houver apenas um item na lista "Tipos_Despesa", repita esse tipo aqui. Se Festa = 0, retorne null."""
    )

    Categoria_Festa: Optional[Literal["CF_1", "CF_2", "CF_3", "CF_4", "CF_5", "CF_6", "CF_7", "CF_8", "CF_9", "CF_10", "CF_0"]] = Field(
        default=None,
        description="""Se Festa = 0, retorne null imediatamente.
        Se Festa = 1, identifique a categoria da festividade 

        HIERARQUIA DE CLASSIFICAÇÃO (SIGA ESTRITAMENTE ESTA ORDEM):
        
        NÍVEL 1 - TEXTO EXPLÍCITO (Prioridade Máxima):
        Se o Histórico contiver menções claras ao tema do evento, classifique na CATEGORIA correta.
        
        NÍVEL 2 - INFERÊNCIA TEMPORAL (Use APENAS se o texto não deixar o tema claro):
        Como um auditor, você TEM AUTORIZAÇÃO para deduzir a CATEGORIA cruzando pistas textuais.
        Se atente que, as vezes, a comemoração de uma festa é adiada ou adiantada alguns poucos dias para cair num dia mais conveniente para o público.
        - Passo A: Se o nome oficial do evento não estiver explícito, deduza a CATEGORIA cruzando a data/pistas sobre o período da festa (ex: apresentação em 25 de dezembro = CF_5, 
        show pirotécnico em 31 de dezembro = CF_6), o perfil da atração (ex: grupo de frevo em fevereiro/março = CF_1), palavras chaves citada no texto, o municício descrito 
        na coluna "ente" com o seu conhecimento interno sobre as festividades normalmente celebradas na Paraíba. (ex: show no município de Guarabira em 17 de Janeiro = CF_3))
        Considere também o nome do município, verifique se a data da festa se encontra no período (normalmente alguns dias antes e/ou após a data) da festa de padroeira da 
        cidade ou de alguma festividade local (ex: apresentação em João Pessoa por volta de 27 de julho e 5 de agosto = CF_3)
        - Passo C: Caso não seja possível identificar a classificação pelo passo acima:  caso a festa aconteça na segunda quinzena do mês de junho, classifique como CF_2

        Caso o texto sugira mais de uma categoria, escolha a categoria que represente a MAIS COERENTE ou o MOTIVO PRINCIPAL do evento.

        CATEGORIAS:
        CF_1 = Carnaval/Pré-Carnaval (Micarande, Micareta, Folia de Rua, Blocos, Trio)
        CF_2 = São João/Festas Juninas (Santo Antônio, São João, São Julho, São Pedro, Arraiá, Quadrilhas, Festejos Juninos, Forró)
        CF_3 = Festa Padroeira/Religiosa (Festividades centradas na devoção popular, celebrando o santo padroeiro do município ou datas sagradas específicas. Envolvem ritos litúrgicos 
        (missas, procissões) e/ou eventos de confraternização e entretenimento em massa. Ex: Católicas: São Sebastião, Nossa Senhora da Conceição, Festa do Carmo, Festa da Luz,
        Romarias, etc. Evangélicas: Dia do Evangélico, Marcha para Jesus, Cruzadas, Shows Gospel, etc).
        CF_4 = Festa Municipal/Aniversário da Cidade/Emancipação Política (Eventos de cunho cívico que celebram a fundação, a autonomia política ou o aniversário de um município.
        Combinam atos oficiais com grandes eventos seculares de entretenimento.)
        CF_5 = Natal (ex: natalino)
        CF_6 = Réveillon (ex: fim de ano, virada)
        CF_7 = Festival de Verão (Eventos focados no turismo e no entretenimento massivo durante a alta estação dos meses de verão. Ex: Fest Verão, Projeto Verão)
        CF_8 = Festival de Inverno (Eventos focados no turismo e no entretenimento massivo durante os meses de inverno, típicas de municípios serranos, de maior altitude ou
        com tradição rural. Ex: Rota Cultural Caminhos do Frio, Festival de Inverno, Festival de Artes do Brejo)
        CF_9 = Feiras, Exposições e palestras e eventos (Eventos com foco direto no fomento econômico, agropecuário, comercial ou na capacitação profissional. Não são focados em
        entretenimento puro, mas em negócios e difusão de conhecimento. Englobam desde feiras de artesanato e grandes exposições agropecuárias (focadas na venda de animais e
        maquinário) até jornadas pedagógicas para servidores. Ex: Exposição Agropecuária, Expo Monteiro, Festa do Bode Rei, Expofeira, Feira de Negócios, Torneios Leiteiros, 
        feiras e exposições em geral, palestras, eventos educacionais, eventos de networking, eventos científicos, eventos esportivos, eventos religiosos sem caráter de festividade, etc)
        CF_10 = Outros (Ex: Festas Tradicionais Locais, confraternizações, premiações, Festa da Laranja, Cavalgada, Vaquejada, desfile cívico, dia das
         mães, dia das crianças, confraternização de funcionários). Utilize APENAS se não encaixar nas opções CF_1 a CF_9.
        CF_0 = Indeterminado. Utilize APENAS se o texto for validado como Festa=1, mas não der nenhuma pista sobre a época ou o tema do evento.
        """
              
    )

    Atracao: Optional[str] = Field(
        default=None, 
        description="""APENAS SE o campo "Tipo_Principal_Despesa" for TD_1 (o intuito da despesa for a contratação (cachê) de uma atração), extraia o nome do artista, 
        cantor ou banda. Para artistas renomados, retorne o "Nome Padrão" mais comercial e reconhecível do artista/cantor/banda, com atenção à grafia. Remova títulos honoríficos
        ou descrições (ex: "Pablo a voz romântica" vira "Pablo", Banda Seu Desejo" vira "Seu Desejo", etc), mas somente se não for alterar o reconhecimento do artista/cantor/banda. 
        Mantenha sempre o nome mais consolidado.
        Se o nome da atração não for explicitamente especificado (ex: "APRESENTAÇÃO MUSICAL", "CANTOR", "BANDAS", "SHOWS", etc, sem citar o nome do artista/cantor/banda), 
        retorne "ARTISTA NÃO ESPECIFICADO".
        Se houver mais de uma atração no empenho, retorne "MÚLTIPLOS ARTISTAS".

        Se a atração for DJ, extraia o nome completo do DJ (ex: DJ Cigano). Caso o nome não esteja especificado (ex: apresentacao artistica DJ nas festividades...), 
        retorne "DJ NÃO ESPECIFICADO".

        IMPORTANTE: SE A LISTA "Tipo_Principal_Despesa" NÃO CONTIVER TD_1 (por exemplo, se o gasto for EXCLUSIVAMENTE para hospedagem, palco ou som para um artista, mas não
        incluir o cachê da atração), RETORNE NULL."""
    )

    Revisao_Manual: Optional[int] = Field(
        default=1, 
        description="""SE a lista gerada para o campo "Tipos_Despesa" tiver mais de um elemento, se a atração for "MÚLTIPLOS ARTISTAS" ou se no histórico houver indicações de 
        múltiplos serviços sendo contratados, retorne 1.
        Caso o empenho se refira a apenas um serviço, a lista em "Tipos_Despesa" possua apenas um elemento e houver apenas uma (ou nenhuma) atração, retorne 0."""
    )

    Locais: Optional[List[str]] = Field(default=None, description="Lista com locais físicos do evento (ex: Praças, Ruas, Ginásios, Sedes). " \
    "NÃO colocar o município do evento, apenas locais físicos. " \
    "Caso não haja detecção do local onde a festa será realizada, a partir das informações do município, data da festa, artista e demais informações da descrição do empenho, " \
    "use o seu conhecimento interno para determinar qual o local onde a festa será realizada.")
    
    Nome_Festa: Optional[str] = Field(default=None, description="Nome oficial do evento descrito (ex: Festividades de Nossa Senhora, Festa da Emancipação).")
    
    Data_Evento: Optional[str] = Field(default=None, description="Data no formato YYYY-MM-DD. Retorne null se não houver nenhuma data mencionada ou implícita. Caso exista mais de uma " \
    "data, escolha a data de início.")


# --- 3. CONFIGURAÇÃO DA IA ---
system_instruction = """
Você é um auditor do Tribunal de Contas da Paraíba especializado em extrair dados estruturados de notas de empenho.
Sua tarefa é analisar os dados fornecidos de uma despesa pública (incluindo Ente e o Histórico) e identificar gastos com festividades públicas, 
com profunda atenção ao vocabulário de eventos do Nordeste do Brasil.
Sua primeira tarefa é avaliar o campo "Festa". 1 se for festividade, 0 caso não seja.
Se "Festa" for 0, pare a análise imediatamente e retorne 'Tipos_Despesa', 'Tipo_Principal_Despesa' e todos os outros campos de extração como null.
IMPORTANTE: O retorno deve ser um JSON estritamente válido.
"""

config_ia = types.GenerateContentConfig(
    system_instruction=system_instruction,
    response_mime_type="application/json",
    response_schema=ProcessoFestividadeFull
)


def analisar_empenho_com_retry(texto_combinado, chunk_id, pbar_chunk, lock_tela, tentativas_max=10):
    espera_atual = 10 # Começa esperando pouco
    for tentativa in range(tentativas_max):
        try:
            # Atualiza o status para mostrar que está processando
            pbar_chunk.set_description(f"[Chunk {chunk_id}] Chamando API...")
        
            #Analisa empenho
            response = client.models.generate_content(
                model='gemini-3.1-flash-lite',
                contents=f"Analise os dados do seguinte empenho:\n{texto_combinado}",
                config=config_ia
            )
            return json.loads(response.text)
            
        except APIError as e:
            #Caso o modelo esteja errado/descontinuado
            if e.code in [400, 404]:
                with lock_tela:
                    pbar_chunk.set_description(f"[Chunk {chunk_id}] [!!!] Erro Fatal (Código {e.code}). Verifique o modelo/prompt.")
                    raise e 
            
            #Caso exceda a cota
            if e.code in [429, 503, 500]:
                with lock_tela:
                    pbar_chunk.set_description(f"[Chunk {chunk_id}] [!] Limite da API atingido ou instabilidade (Cód {e.code}). Pausa de {espera_atual}s...")
                time_module.sleep(espera_atual)
                espera_atual *= 2 # Backoff exponencial (5s, 10s, 20s...)
                continue 
            else:
                with lock_tela:
                    pbar_chunk.set_description(f"[Chunk {chunk_id}] [!] Erro na API (Código: {e.code}). Tentando novamente em {espera_atual}s...")
                time_module.sleep(espera_atual)
                
        except Exception as e:
            with lock_tela:
                pbar_chunk.set_description(f"[Chunk {chunk_id}] [x] Erro inesperado: {e}")
            time_module.sleep(5)
            
    raise Exception("Falha após múltiplas tentativas. Servidor da Google sobrecarregado.")

def limpar_campo(valor):
    """Função auxiliar para tratar valores vazios/NaN provenientes do Pandas"""
    if pd.isna(valor) or str(valor).strip().lower() == "nan":
        return "Não informado"
    return str(valor).strip()

# --- 4. LÓGICA DE EXECUÇÃO EM THREADS ---
def iniciar_processamento(args):
    chunk_id, df_chunk, pbar_main, lock_tela = args
    caminho_csv_chunk = f"./resultados/Resultados_temp_chunk_{chunk_id}.csv"
    os.makedirs("./resultados", exist_ok=True)
    df_resultados = pd.DataFrame()
    total_linhas_chunk = len(df_chunk)

    with tqdm(total=total_linhas_chunk, position=chunk_id + 2, leave=False, ncols=100) as pbar_chunk:
        try:
            for idx, (index_original, linha) in enumerate(df_chunk.iterrows(), start=1):
                
                # Atualiza o status 
                with lock_tela:
                    pbar_chunk.set_description(f"[Chunk {chunk_id}] Processando linha {idx}/{total_linhas_chunk}")
                
                linha_original = linha.to_dict()
                
                # Extração dos campos e tratamento de vazios
                ente = limpar_campo(linha_original.get(COLUNA_ENTE))
                historico = limpar_campo(linha_original.get(COLUNA_HISTORICO))
                
                if historico == "Não informado":
                    dados_extraidos = {"Festa": 0}
                else:
                    # Combinando os dados em um bloco estruturado para a IA
                    texto_para_ia = f"Histórico: {historico}\nEnte: {ente}"
                    dados_extraidos = analisar_empenho_com_retry(texto_para_ia, chunk_id, pbar_chunk, lock_tela)
                    
                linha_final = {**linha_original, **dados_extraidos}
                df_linha = pd.DataFrame([linha_final])
                
                # Salva no CSV
                arquivo_existe = os.path.exists(caminho_csv_chunk)
                df_linha.to_csv(caminho_csv_chunk, mode='a', header=not arquivo_existe, index=False)
                df_resultados = pd.concat([df_resultados, df_linha], ignore_index=True)

                # Atualiza o status 
                with lock_tela:
                    pbar_chunk.update(1)  
                    pbar_main.update(1)
            
            pbar_chunk.set_description(f"[Chunk {chunk_id}] concluído!")
            return df_resultados

        except Exception as e:
            pbar_chunk.set_description(f"\n[!!!] CHUNK {chunk_id} INTERROMPIDO. Motivo: {e}")
            return df_resultados

# --- 5. ORQUESTRAÇÃO ---
if __name__ == "__main__":
    os.makedirs("./resultados", exist_ok=True)
    
    # 1. Carregamento da Base

    # Ler a coluna CPF/CNPJ como string para não perder os zeros a esquerda
    # Lê apenas a linha 0 (cabeçalho) para pegar os nomes das colunas
    colunas = pd.read_csv(CAMINHO_ENTRADA, nrows=0, delimiter=';').columns

    # Procura qual coluna tem 'cpf' ou 'cnpj' no nome (ignorando maiúsculas/minúsculas)
    nome_coluna_alvo = None
    for col in colunas:
        if 'cpf' in col.lower() or 'cnpj' in col.lower():
            nome_coluna_alvo = col
            break

    # Se achou a coluna, lê o arquivo passando o dtype dinâmico
    if nome_coluna_alvo:
        df_entrada = pd.read_csv(CAMINHO_ENTRADA, delimiter=';', dtype={nome_coluna_alvo: str})
        print(f"Sucesso! A coluna '{nome_coluna_alvo}' foi lida como texto.")
    else:
        print("Nenhuma coluna de CPF/CNPJ encontrada.")



    df_entrada = df_entrada.dropna(subset=[COLUNA_HISTORICO]).reset_index(drop=True)
    df_entrada['ID_Global'] = df_entrada.index 
    
    #df_entrada = df_entrada[:10] # Teste com 1000 linhas
    total_linhas_originais = len(df_entrada)

    #Para preservar os cpfs/cnpjs
    if 'CPF/CNPJ' in df_entrada.columns:
        df_entrada['CPF/CNPJ'] = df_entrada['CPF/CNPJ'].astype(str) 

    # 2. CHECKPOINT GLOBAL
    print("Verificando checkpoints...")
    dfs_processados = []
    arquivos_temp = glob.glob("./resultados/Resultados_temp_chunk_*.csv")
    
    for arq in arquivos_temp:
        try:
            df_tmp = pd.read_csv(arq)
            if not df_tmp.empty and 'ID_Global' in df_tmp.columns:
                dfs_processados.append(df_tmp)
        except: pass
            
    df_checkpoint_completo = pd.DataFrame()
    qtd_ja_processados = 0

    if dfs_processados:
        df_checkpoint_completo = pd.concat(dfs_processados, ignore_index=True).drop_duplicates(subset=['ID_Global'])
        
        ids_ja_processados = df_checkpoint_completo['ID_Global'].tolist()
        qtd_ja_processados = len(ids_ja_processados)
        df_entrada = df_entrada[~df_entrada['ID_Global'].isin(ids_ja_processados)]
        print(f"-> {len(ids_ja_processados)} registros já processados. Restam {len(df_entrada)} inéditos.")

    # 3. MÁXIMA VELOCIDADE COM THREADS
    if not df_entrada.empty:
        # Divide os dados em N pedaços com base no número de Threads configurado
        n_pedacos = min(len(df_entrada), MAX_WORKERS) 
        indices_chunks = np.array_split(range(len(df_entrada)), n_pedacos)
        chunks = [df_entrada.iloc[indices] for indices in indices_chunks if len(indices) > 0]
        #args_processamento = [(i, chunk) for i, chunk in enumerate(chunks)]
        
        print(f"Acelerando processamento com {len(chunks)} Threads simultâneas...")
        
       # Lock pra organizar as barras de progresso
        lock_tela = Lock()

        # A barra principal fica ancorada no topo (position=0)
        with tqdm(total=total_linhas_originais, initial=qtd_ja_processados, desc="Progresso Geral", position=0, leave=True, ncols=100) as pbar_main:
            args_processamento = [(i, chunk, pbar_main, lock_tela) for i, chunk in enumerate(chunks)]
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                resultados_novos = list(executor.map(iniciar_processamento, args_processamento))

        # Pula as linhas para não sobescrever o resultado final em cima das barras apagadas
        print("\n" * (MAX_WORKERS + 2))

        #Unifica os resultados   
        df_novos = pd.concat(resultados_novos, ignore_index=True) if resultados_novos else pd.DataFrame()
    else:
        print("\nTodos os dados já estavam processados!")
        df_novos = pd.DataFrame()


    # 4. FINALIZAÇÃO
    df_final = pd.concat([df_checkpoint_completo, df_novos], ignore_index=True)
    
    if not df_final.empty:
        df_final = df_final.sort_values('ID_Global').reset_index(drop=True)
        salvar_sem_sobrescrever(df_final, CAMINHO_SAIDA_CSV, 'csv')
        print(f"\nSucesso! Arquivo final gerado.")
        
        # Correção: Atualiza a lista de arquivos temp ANTES de apagar
        if len(df_final) >= total_linhas_originais:
            print("Limpando arquivos temporários...")
            arquivos_para_apagar = glob.glob("./resultados/Resultados_temp_chunk_*.csv")
            
            for arq in arquivos_para_apagar:
                try:
                    if os.path.exists(arq): 
                        os.remove(arq)
                except Exception as e:
                    print(f"Não foi possível excluir o arquivo {arq}. Ele pode estar aberto em outro programa. Erro: {e}")
    else:
        print("Nenhum dado para salvar.")
