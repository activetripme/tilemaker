# План: Реализация Offset Curved для размещения подписей рек в tilemaker

## Контекст

Сейчас tilemaker записывает подписи рек как атрибуты (`name`) на LINESTRING-объектах. Рендерер (MapLibre GL) размещает текст вдоль линии, но для извилистых рек результат неудовлетворительный — текст "дрожит" на поворотах или пропадает. Алгоритм Offset Curved (из документа `Offset Curved.md`) решает это: предварительная обработка геометрии создаёт сглаженную направляющую линию для текста.

**Цель**: Добавить в tilemaker возможность генерировать сглаженные/смещённые направляющие линии (LINESTRING) для подписей рек, которые рендерер использует для размещения текста.

## Архитектура

- **C++**: Геометрический алгоритм (новые файлы) + интеграция в Lua-bridge
- **Lua API**: Новая функция `LayerAsOffsetCurve(layerName, options_table)` — возвращает `true`/`nil`
- **Два режима**: `on_line` (вдоль русла) и `offset` (со смещением)
- **Выход**: LINESTRING в векторном тайле с текстовым атрибутом `name`

## Изменяемые файлы

| Файл | Действие |
|------|----------|
| `include/offset_curve.h` | **Новый** — API алгоритма |
| `src/offset_curve.cpp` | **Новый** — реализация алгоритма |
| `include/osm_lua_processing.h` | Добавить объявление `LayerAsOffsetCurve` |
| `src/osm_lua_processing.cpp` | Добавить raw-обёртку, регистрацию Kaguya, реализацию метода |
| `Makefile` | Добавить `src/offset_curve.o` |
| `CMakeLists.txt` | Добавить `src/offset_curve.cpp` |
| `config.json` / `resources/config-openmaptiles.json` | Добавить слой `waterway_label` |
| `process.lua` / `resources/process-openmaptiles.lua` | Вызывать `LayerAsOffsetCurve` для рек |

## Этапы реализации

### Этап 1: Ядро алгоритма (новые файлы)

**`include/offset_curve.h`** — структура параметров и объявления функций:

```cpp
struct OffsetCurveParams {
    double textWidthM;       // ширина текста в метрах
    double fontSize;         // размер шрифта (для вычисления offset)
    double offsetDistanceM;  // расстояние смещения (0 = on_line)
    double flatnessWeight;   // вес метрики плоскостности (default 1.0)
    double avdistWeight;     // вес метрики расстояния (default 1.0)
    double maxAngleDeg;      // макс. угол поворота (default 45°)
    double simplifyTolerance;// допуск Douglas-Peucker
    int smoothingIterations; // итераций сглаживания Chaikin (default 3)
};

struct PlacementResult {
    Linestring guideLine;  // направляющая линия
    double score;          // штраф лучшего кандидата
    bool isOffset;         // применено ли смещение
};

bool computeOffsetCurvePlacement(
    const Linestring& inputLine,
    const OffsetCurveParams& params,
    PlacementResult& result
);
```

**`src/offset_curve.cpp`** — реализация 5 шагов алгоритма:

1. **Упрощение + сглаживание**: `boost::geometry::simplify()` (существующая) → Chaikin corner-cutting (новая). Chaikin: для каждого сегмента (P[i], P[i+1]) генерируем точки на 1/4 и 3/4. O(N) за итерацию, ~3 итерации.

2. **Генерация кандидатов**: Параметризация по длине дуги, скользящее окно с шагом `textWidth/8`, длина окна `textWidth * 1.2`.

3. **Оценка кандидатов**:
   - **Flatness**: площадь между кривой кандидата и хордой (прямая между концами)
   - **AveDist**: среднее расстояние до исходной линии (для offset-режима)
   - **Max Angle**: `atan2` между соседними сегментами, отсев при превышении порога

4. **Offset**: нормаль к каждому участку, смещение на δ. Если self-intersection — пробуем другую сторону.

5. **Выбор лучшего**: минимум взвешенного штрафа.

### Этап 2: Интеграция в Lua-bridge

**`include/osm_lua_processing.h`** — добавить после строки 197:
```cpp
bool LayerAsOffsetCurve(const std::string &layerName, kaguya::VariadicArgType options);
```

**`src/osm_lua_processing.cpp`** — 3 точки изменений:

1. **Raw-обёртка** (после строки 194):
```cpp
bool rawLayerAsOffsetCurve(const std::string &layerName, kaguya::VariadicArgType options) {
    return osmLuaProcessing->LayerAsOffsetCurve(layerName, options);
}
```

2. **Регистрация Kaguya** (после строки 271):
```cpp
luaState["LayerAsOffsetCurve"] = &rawLayerAsOffsetCurve;
```

3. **Реализация метода** — по паттерну `LayerAsCentroid`:
   - Получить linestring через `linestringCached()` (для way) или `multiLinestringCached()` (для relation)
   - Распарсить Lua-таблицу options → `OffsetCurveParams`
   - Вызвать `computeOffsetCurvePlacement()`
   - Если `false` — вернуть `false` (Lua fallback на Layer/LayerAsCentroid)
   - Если `true` — сохранить результат через `osmMemTiles.storeLinestring()`, создать `OutputObject(LINESTRING_, ...)`, установить атрибут `name`

### Этап 3: Сборка

**Makefile** — добавить `src/offset_curve.o` в список зависимостей tilemaker (строка 87-135).

**CMakeLists.txt** — добавить `src/offset_curve.cpp` в список источников.

### Этап 4: Конфигурация слоёв и Lua-скрипт

**config.json** — добавить слой:
```json
"waterway_label": {
    "minzoom": 9,
    "maxzoom": 14,
    "simplify_below": 12,
    "simplify_level": 0.0003
}
```

**process-openmaptiles.lua** — в секции waterway (около строки 621), для рек с `name`:
```lua
-- Попытка Offset Curved, fallback на обычный способ
local placed = LayerAsOffsetCurve("waterway_label", {
    font_size = 12,
    char_width = 7,
    text_width_m = Find("name"):len() * 7 * ZRES[zoom],
    offset = 0,
    mode = "on_line"
})
if not placed then
    -- fallback: обычное размещение вдоль линии
    Layer("water_name", false)
    Attribute("class", waterway)
    SetNameAttributes()
end
```

## Lua API

```lua
LayerAsOffsetCurve(layerName, options)
```

**Параметры `options` (Lua-таблица)**:
| Ключ | Тип | Default | Описание |
|------|-----|---------|----------|
| `font_size` | number | 12 | Размер шрифта в пикселях |
| `char_width` | number | 7 | Ширина символа в пикселях |
| `text_width_m` | number | auto | Ширина текста в метрах |
| `offset` | number | 0 | Смещение в пикселях (0 = on_line) |
| `mode` | string | "on_line" | "on_line" или "offset" |
| `max_angle` | number | 45 | Макс. угол поворота в градусах |

**Возвращает**: `true` если размещение найдено, `nil` если нет.

## Потокобезопасность

Алгоритм `computeOffsetCurvePlacement()` чисто функциональный — нет разделяемого состояния. `OsmLuaProcessing` — по экземпляру на поток. `osmMemTiles.storeLinestring()` уже потокобезопасен. Новая синхронизация не нужна.

## Метрики шрифта

Tilemaker не рендерит текст, поэтому реальная ширина текста неизвестна. Решение:
- Lua оценивает ширину как `len(name) * char_width * meters_per_pixel_at_zoom`
- Рендерер всё равно использует `text-max-width` для коррекции
- Направляющая линия даёт форму кривой; точная длина менее критична

## Проверка

1. `make` — сборка без ошибок
2. Запуск на тестовом экстракте: `./tilemaker --input test.pbf --output test.mbtiles --config config.json --process process.lua`
3. Проверить наличие слоя `waterway_label` в выходных тайлах через `vector-tile-js` или `tippecanoe-decode`
4. Визуальная проверка в MapLibre GL с `symbol-placement: line` на слое `waterway_label`
