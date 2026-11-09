//+------------------------------------------------------------------+
//|                                                   SMC_Bridge.mq5 |
//|  Міст MT5 -> SMC App: свічки H4/H1/M15 і ціни валютних пар у     |
//|  файли спільної папки MT5 (Common\Files\SMC).                    |
//|  НІЧОГО НЕ ТОРГУЄ: лише читає історію та ціни.                   |
//+------------------------------------------------------------------+
#property copyright   "SMC App"
#property version     "1.00"
#property description "Вивантажує свічки H4/H1/M15 і ціни валютних пар для SMC App. Угод не відкриває."

input string InpPairs     = "EURUSD,GBPUSD,USDJPY,USDCHF,AUDUSD,USDCAD,NZDUSD,EURGBP,EURJPY,EURCHF,EURAUD,EURCAD,EURNZD,GBPJPY,GBPCHF,GBPAUD,GBPCAD,GBPNZD,AUDJPY,AUDCHF,AUDCAD,AUDNZD,NZDJPY,NZDCHF,NZDCAD,CADJPY,CADCHF,CHFJPY"; // Пари (якщо SMC App не передав свій список)
input int    InpBars      = 600;  // Скільки свічок на кожен таймфрейм
input int    InpPriceSec  = 5;    // Ціни: раз на N секунд
input int    InpCandleSec = 60;   // Свічки: раз на N секунд

#define DIR "SMC"
string   g_pairs[];
string   g_names[];
datetime g_last_candles = 0;
string   g_pairs_src = "";

//+------------------------------------------------------------------+
string Trim(string s)
  {
   StringTrimLeft(s);
   StringTrimRight(s);
   return s;
  }

//+------------------------------------------------------------------+
//| Фактична назва символу в брокера: EURUSD, EURUSD.r, EURUSDm ...  |
//+------------------------------------------------------------------+
string ResolveSymbol(string pair)
  {
   int total = SymbolsTotal(false);
   string best = "";
   int best_score = -1;
   for(int i = 0; i < total; i++)
     {
      string s = SymbolName(i, false);
      if(StringFind(s, pair) != 0 || StringLen(s) > StringLen(pair) + 4)
         continue;
      int score = 0;
      if(s == pair)
         score += 4;
      if(SymbolInfoInteger(s, SYMBOL_VISIBLE))
         score += 2;
      if(SymbolInfoInteger(s, SYMBOL_TRADE_MODE) != SYMBOL_TRADE_MODE_DISABLED)
         score += 1;
      if(score > best_score)
        {
         best = s;
         best_score = score;
        }
     }
   return best;
  }

//+------------------------------------------------------------------+
//| Список пар: з файла SMC\pairs.txt (його пише SMC App) або з input |
//+------------------------------------------------------------------+
void LoadPairs()
  {
   string src = InpPairs;
   int h = FileOpen(DIR + "\\pairs.txt", FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h != INVALID_HANDLE)
     {
      string t = Trim(FileReadString(h));
      FileClose(h);
      if(StringLen(t) >= 6)
         src = t;
     }
   if(src == g_pairs_src && ArraySize(g_pairs) > 0)
      return;
   g_pairs_src = src;
   string raw[];
   int n = StringSplit(src, ',', raw);
   ArrayResize(g_pairs, 0);
   ArrayResize(g_names, 0);
   for(int i = 0; i < n; i++)
     {
      string p = Trim(raw[i]);
      StringToUpper(p);
      if(StringLen(p) < 6)
         continue;
      int k = ArraySize(g_pairs);
      ArrayResize(g_pairs, k + 1);
      ArrayResize(g_names, k + 1);
      g_pairs[k] = p;
      g_names[k] = ResolveSymbol(p);
      if(g_names[k] != "")
         SymbolSelect(g_names[k], true);
     }
  }

//+------------------------------------------------------------------+
//| Атомарний запис: спершу .tmp, потім заміна (SMC App не прочитає  |
//| половину файла)                                                  |
//+------------------------------------------------------------------+
bool WriteFile(string name, string text)
  {
   string tmp = DIR + "\\" + name + ".tmp";
   int h = FileOpen(tmp, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)
      return false;
   FileWriteString(h, text);
   FileClose(h);
   return FileMove(tmp, FILE_COMMON, DIR + "\\" + name, FILE_COMMON | FILE_REWRITE);
  }

//+------------------------------------------------------------------+
void WriteCandles()
  {
   g_last_candles = TimeLocal();
   ENUM_TIMEFRAMES tfs[3] = {PERIOD_H4, PERIOD_H1, PERIOD_M15};
   string tfn[3] = {"H4", "H1", "M15"};
   for(int i = 0; i < ArraySize(g_pairs); i++)
     {
      if(g_names[i] == "")
         continue;
      int dg = (int)SymbolInfoInteger(g_names[i], SYMBOL_DIGITS);
      for(int k = 0; k < 3; k++)
        {
         MqlRates r[];
         int got = CopyRates(g_names[i], tfs[k], 0, InpBars, r);
         if(got <= 0)
            continue;   // історія ще вантажиться — наступного разу
         string text = "";
         for(int j = 0; j < got; j++)
            text += IntegerToString((long)r[j].time) + "," + DoubleToString(r[j].open, dg) + "," + DoubleToString(r[j].high, dg) + ","
                    + DoubleToString(r[j].low, dg) + "," + DoubleToString(r[j].close, dg) + "," + IntegerToString((long)r[j].tick_volume) + "\n";
         WriteFile(g_pairs[i] + "_" + tfn[k] + ".csv", text);
        }
     }
  }

//+------------------------------------------------------------------+
string JsonStr(string s)
  {
   StringReplace(s, "\\", "\\\\");
   StringReplace(s, "\"", "\\\"");
   return "\"" + s + "\"";
  }

//+------------------------------------------------------------------+
void WriteStatus()
  {
   string items = "";
   for(int i = 0; i < ArraySize(g_pairs); i++)
     {
      string nm = g_names[i];
      MqlTick t;
      bool ok = (nm != "") && SymbolInfoTick(nm, t) && t.bid > 0;
      int dg = (nm != "") ? (int)SymbolInfoInteger(nm, SYMBOL_DIGITS) : 5;
      if(items != "")
         items += ",";
      items += "{\"pair\":" + JsonStr(g_pairs[i]) + ",\"name\":" + JsonStr(nm) + ",\"ok\":" + (ok ? "true" : "false")
               + ",\"digits\":" + IntegerToString(dg)
               + ",\"bid\":" + (ok ? DoubleToString(t.bid, dg) : "0") + ",\"ask\":" + (ok ? DoubleToString(t.ask, dg) : "0")
               + ",\"tick_time\":" + (ok ? IntegerToString((long)t.time) : "0") + "}";
     }
   string js = "{\"v\":1,\"ea_version\":\"1.00\",\"gmt\":" + IntegerToString((long)TimeGMT())
               + ",\"server_time\":" + IntegerToString((long)TimeTradeServer())
               + ",\"server\":" + JsonStr(AccountInfoString(ACCOUNT_SERVER))
               + ",\"company\":" + JsonStr(AccountInfoString(ACCOUNT_COMPANY))
               + ",\"bars\":" + IntegerToString(InpBars)
               + ",\"pairs\":[" + items + "]}";
   WriteFile("status.json", js);
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   FolderCreate(DIR, FILE_COMMON);
   LoadPairs();
   EventSetTimer(MathMax(1, InpPriceSec));
   WriteCandles();
   WriteStatus();
   Print("SMC_Bridge: пар ", ArraySize(g_pairs), ", файли в Common\\Files\\", DIR, " (угод не відкриваю)");
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   EventKillTimer();
  }

//+------------------------------------------------------------------+
void OnTimer()
  {
   if(TimeLocal() - g_last_candles >= InpCandleSec)
     {
      LoadPairs();
      WriteCandles();
     }
   WriteStatus();
  }
//+------------------------------------------------------------------+
