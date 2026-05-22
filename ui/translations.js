/**
 * translations.js — Multi-language support for AI Memory System dashboard
 */

export const translations = {
  en: {
    // Header & Navigation
    'header.title': 'AI Memory System',
    'header.project': 'Project',
    'header.refresh': 'Refresh data',
    
    // Tabs
    'tab.dashboard': 'Dashboard',
    'tab.entries': 'Entries',
    'tab.conflicts': 'Conflicts',
    'tab.graph': 'Graph',
    'tab.files': 'Files',
    'tab.settings': 'Settings',
    
    // Dashboard - KPI Cards
    'kpi.totalEntries': 'Total Entries',
    'kpi.bugFixes': 'Bug Fixes',
    'kpi.features': 'Features',
    'kpi.decisions': 'Decisions',
    'kpi.avgConfidence': 'Avg Confidence',
    'kpi.openConflicts': 'Open Conflicts',
    'kpi.linkedEntries': 'Linked Entries',
    'kpi.activeEntries': 'Active Entries',
    
    // Dashboard - Conflict Banner
    'dashboard.conflictBanner': 'Open Conflicts:',
    'dashboard.unresolved': 'unresolved —',
    'dashboard.resolveNow': 'resolve now',
    
    // Dashboard - Charts
    'chart.entryTypes': 'Entry Types',
    'chart.activity': 'Activity (last 30 days)',
    'chart.topFiles': 'Top 10 Files',
    'chart.confidenceDistribution': 'Confidence Distribution',
    'chart.topTags': 'Top Tags',
    'chart.statusBreakdown': 'Status Breakdown',
    'chart.entries': 'Entries',
    'chart.count': 'Count',
    
    // Confidence ranges
    'conf.0-10': '0–10%',
    'conf.10-20': '10–20%',
    'conf.20-30': '20–30%',
    'conf.30-40': '30–40%',
    'conf.40-50': '40–50%',
    'conf.50-60': '50–60%',
    'conf.60-70': '60–70%',
    'conf.70-80': '70–80%',
    'conf.80-90': '80–90%',
    'conf.90-100': '90–100%',
    
    // Entries table
    'entries.noEntriesFound': 'No entries found.',
    'entries.type': 'Type',
    'entries.status': 'Status',
    'entries.confidence': 'Confidence',
    'entries.description': 'Description',
    'entries.files': 'Files',
    'entries.date': 'Date',
    'entries.search': 'Search entries...',
    'entries.filterByType': 'Filter by type',
    'entries.filterByStatus': 'Filter by status',
    'entries.all': 'All',
    
    // Entry types
    'type.bug_fix': 'bug fix',
    'type.feature': 'feature',
    'type.decision': 'decision',
    'type.note': 'note',
    
    // Entry status
    'status.active': 'active',
    'status.resolved': 'resolved',
    'status.superseded': 'superseded',
    'status.conflict': 'conflict',
    
    // Entry detail panel
    'detail.id': 'ID',
    'detail.description': 'Description',
    'detail.cause': 'Cause',
    'detail.fix': 'Fix',
    'detail.files': 'Files',
    'detail.confidence': 'Confidence',
    'detail.status': 'Status',
    'detail.timestamp': 'Timestamp',
    'detail.tags': 'Tags',
    'detail.dependsOn': 'Depends On',
    'detail.requiredBy': 'Required By',
    'detail.testIds': 'Test IDs',
    'detail.conflicts': 'Conflicts',
    'detail.edit': 'Edit',
    'detail.save': 'Save',
    'detail.cancel': 'Cancel',
    'detail.delete': 'Delete',
    'detail.noTags': 'No tags',
    'detail.noLinks': 'No links',
    
    // Conflicts tab
    'conflicts.noConflicts': 'No conflicts found.',
    'conflicts.conflict': 'Conflict',
    'conflicts.similarity': 'Similarity',
    'conflicts.entryA': 'Entry A',
    'conflicts.entryB': 'Entry B',
    'conflicts.resolveWith': 'Resolve with',
    'conflicts.action': 'Action',
    'conflicts.resolve': 'Resolve',
    'conflicts.resolve_title': 'Resolve Conflict',
    'conflicts.supersede_a': 'Supersede A',
    'conflicts.supersede_b': 'Supersede B',
    'conflicts.merge': 'Merge',
    'conflicts.dismiss': 'Dismiss',
    'conflicts.reason': 'Reason',
    'conflicts.enter_reason': 'Enter reason for resolution...',
    'conflicts.open': 'open',
    'conflicts.total': 'total',
    'conflicts.compareTitle': 'Compare Conflict Entries',
    'conflicts.ok': 'OK',
    'conflicts.cancel': 'Cancel',
    'conflicts.close': 'Close',
    
    // Graph tab
    'graph.title': 'Dependency Graph',
    'graph.filterByType': 'Filter by type',
    'graph.hideDuplicate': 'Hide duplicate nodes',
    'graph.hideSuperseded': 'Hide superseded entries',
    'graph.minConfidence': 'Min confidence',
    'graph.suggestLinks': 'Suggest links',
    'graph.nodes': 'nodes',
    'graph.edges': 'edges',
    
    // Files tab
    'files.name': 'File Name',
    'files.entries': 'Entries',
    'files.search': 'Search files...',
    'files.noFiles': 'No files found.',
    'files.summary': 'Summary',
    'files.fileEntries': 'File Entries',
    'files.noSummary': 'No summary available.',
    'files.sortByName': 'Sort by name',
    'files.sortByCount': 'Sort by count',
    
    // Settings tab
    'settings.title': 'Settings',
    'settings.systemSettings': 'System Settings',
    'settings.saveSettings': 'Save Settings',
    'settings.tagEditor': 'Tag Editor',
    'settings.addTag': 'Add tag',
    'settings.operations': 'Operations',
    'settings.decay': 'Decay',
    'settings.deduplicate': 'Deduplicate',
    'settings.renderWiki': 'Render Wiki',
    'settings.lint': 'Lint',
    'settings.dryRun': 'Dry run',
    'settings.apply': 'Apply',
    'settings.running': 'Running...',
    'settings.complete': 'Complete',
    'settings.error': 'Error',
    'settings.newTag': 'Enter new tag',
    
    // Settings - Detailed sections
    'settings.resetDefaults': 'Reset to defaults',
    'settings.confidenceDecay': 'Confidence Decay',
    'settings.enableDecay': 'Enable decay',
    'settings.halfLife': 'Half-life (days)',
    'settings.minConfFloor': 'Minimum confidence floor',
    'settings.semanticDeduplicate': 'Semantic Deduplication',
    'settings.enableDeduplicate': 'Enable deduplication',
    'settings.similarityThreshold': 'Similarity threshold',
    'settings.revertDetection': 'Revert Detection',
    'settings.enableRevertDetection': 'Enable revert detection',
    'settings.revertPairThreshold': 'Revert pair threshold',
    'settings.staleEntryCheck': 'Stale Entry Check',
    'settings.minEntryAge': 'Minimum entry age (days)',
    'settings.wiki': 'Wiki',
    'settings.autoRenderWiki': 'Auto-render wiki on add_memory',
    'settings.query': 'Query',
    'settings.defaultTopK': 'Default top-k results',
    'settings.decayBlendWeight': 'Decay blend weight (0–1)',
    'settings.defaultTags': 'Default Tags',
    'settings.addNewTag': '+ Add',
    'settings.dryRun': 'Dry run',
    
    // Operations panel
    'op.decayDesc': 'Apply time-based decay to stale entries.',
    'op.dedupDesc': 'Find and merge near-duplicate entries.',
    'op.wikiDesc': 'Regenerate all markdown wiki pages.',
    'op.lintDesc': 'Run health checks on the memory store.',
    'op.runDecay': 'Run Decay',
    'op.runDedup': 'Run Deduplication',
    'op.renderWiki': 'Render Wiki',
    'op.runLint': 'Run Lint',
    'op.lintCheck': 'Lint Check',
    
    // Conflict card labels
    'conflicts.cardConflict': 'Conflict',
    'conflicts.cardSimilarity': 'Similarity',
    'conflicts.cardResolved': 'resolved',
    
    // Graph filters and controls
    'graph.filterType': 'Filter type:',
    'graph.pausePhysics': '⏸ Pause physics',
    'graph.fit': '⊡ Fit',
    'graph.reload': '↻ Reload',
    'graph.hideSuperseded': 'Hide superseded',
    'graph.decision': 'Decision',
    'graph.bugFix': 'Bug Fix',
    'graph.feature': 'Feature',
    'graph.note': 'Note',
    
    // Entries filters
    'entries.filterType': 'Filter type:',
    'entries.decision': 'Decision',
    'entries.bugFix': 'Bug Fix',
    'entries.feature': 'Feature',
    'entries.note': 'Note',
    'entries.filterStatus': 'Status:',
    'entries.hideSuperseded': 'Hide superseded',
    'entries.minConf': 'Min conf:',
    
    // Conflict table header
    'conflicts.type': 'Type',
    'conflicts.status': 'Status',
    'conflicts.confidence': 'Confidence',
    'conflicts.description': 'Description',
    
    // Other UI elements
    'ui.translate': 'Translate to Ukrainian',
    'ui.resizable': 'Resizable window',
    
    // Notifications
    'toast.success': 'Success',
    'toast.error': 'Error',
    'toast.info': 'Info',
    'toast.warning': 'Warning',
    
    // Dialog
    'dialog.confirm': 'Confirm',
    'dialog.cancel': 'Cancel',
    'dialog.yes': 'Yes',
    'dialog.no': 'No',
    
    // Loading & Empty states
    'empty.loading': 'Loading...',
    'empty.noData': 'No data available',
    'empty.noSelection': 'No selection',
    
    // Language
    'lang.en': 'English',
    'lang.uk': 'Українська',
  },
  
  uk: {
    // Header & Navigation
    'header.title': '🧠 Система Пам\'яті ШІ',
    'header.project': 'Проект',
    'header.refresh': 'Оновити дані',
    
    // Tabs
    'tab.dashboard': 'Панель',
    'tab.entries': 'Записи',
    'tab.conflicts': 'Конфлікти',
    'tab.graph': 'Граф',
    'tab.files': 'Файли',
    'tab.settings': 'Параметри',
    
    // Dashboard - KPI Cards
    'kpi.totalEntries': 'Всього записів',
    'kpi.bugFixes': 'Виправлень помилок',
    'kpi.features': 'Функцій',
    'kpi.decisions': 'Рішень',
    'kpi.avgConfidence': 'Середня впевненість',
    'kpi.openConflicts': 'Відкритих конфліктів',
    'kpi.linkedEntries': 'Пов\'язаних записів',
    'kpi.activeEntries': 'Активних записів',
    
    // Dashboard - Conflict Banner
    'dashboard.conflictBanner': 'Відкриті конфлікти:',
    'dashboard.unresolved': 'невирішених —',
    'dashboard.resolveNow': 'розв\'язати зараз',
    
    // Dashboard - Charts
    'chart.entryTypes': 'Типи записів',
    'chart.activity': 'Діяльність (останніх 30 днів)',
    'chart.topFiles': 'Топ 10 файлів',
    'chart.confidenceDistribution': 'Розподіл впевненості',
    'chart.topTags': 'Найпопулярніші теги',
    'chart.statusBreakdown': 'Розбивка за статусом',
    'chart.entries': 'Записи',
    'chart.count': 'Кількість',
    
    // Confidence ranges
    'conf.0-10': '0–10%',
    'conf.10-20': '10–20%',
    'conf.20-30': '20–30%',
    'conf.30-40': '30–40%',
    'conf.40-50': '40–50%',
    'conf.50-60': '50–60%',
    'conf.60-70': '60–70%',
    'conf.70-80': '70–80%',
    'conf.80-90': '80–90%',
    'conf.90-100': '90–100%',
    
    // Entries table
    'entries.noEntriesFound': 'Записів не знайдено.',
    'entries.type': 'Тип',
    'entries.status': 'Статус',
    'entries.confidence': 'Впевненість',
    'entries.description': 'Опис',
    'entries.files': 'Файли',
    'entries.date': 'Дата',
    'entries.search': 'Пошук записів...',
    'entries.filterByType': 'Фільтр за типом',
    'entries.filterByStatus': 'Фільтр за статусом',
    'entries.all': 'Всі',
    
    // Entry types
    'type.bug_fix': 'виправлення помилки',
    'type.feature': 'функція',
    'type.decision': 'рішення',
    'type.note': 'примітка',
    
    // Entry status
    'status.active': 'активний',
    'status.resolved': 'розв\'язано',
    'status.superseded': 'заміщено',
    'status.conflict': 'конфлікт',
    
    // Entry detail panel
    'detail.id': 'ID',
    'detail.description': 'Опис',
    'detail.cause': 'Причина',
    'detail.fix': 'Виправлення',
    'detail.files': 'Файли',
    'detail.confidence': 'Впевненість',
    'detail.status': 'Статус',
    'detail.timestamp': 'Часова мітка',
    'detail.tags': 'Теги',
    'detail.dependsOn': 'Залежить від',
    'detail.requiredBy': 'Потребується для',
    'detail.testIds': 'ID тестів',
    'detail.conflicts': 'Конфлікти',
    'detail.edit': 'Редагувати',
    'detail.save': 'Зберегти',
    'detail.cancel': 'Скасувати',
    'detail.delete': 'Видалити',
    'detail.noTags': 'Немає тегів',
    'detail.noLinks': 'Немає посилань',
    
    // Conflicts tab
    'conflicts.noConflicts': 'Конфліктів не знайдено.',
    'conflicts.conflict': 'Конфлікт',
    'conflicts.similarity': 'Подібність',
    'conflicts.entryA': 'Запис A',
    'conflicts.entryB': 'Запис B',
    'conflicts.resolveWith': 'Розв\'язати з',
    'conflicts.action': 'Дія',
    'conflicts.resolve': 'Розв\'язати',
    'conflicts.resolve_title': 'Розв\'язати конфлікт',
    'conflicts.supersede_a': 'Замістити A',
    'conflicts.supersede_b': 'Замістити B',
    'conflicts.merge': 'Об\'єднати',
    'conflicts.dismiss': 'Відхилити',
    'conflicts.reason': 'Причина',
    'conflicts.enter_reason': 'Введіть причину розв\'язання...',
    'conflicts.open': 'відкритих',
    'conflicts.total': 'всього',
    'conflicts.compareTitle': 'Порівняння записів конфлікту',
    'conflicts.ok': 'OK',
    'conflicts.cancel': 'Скасувати',
    'conflicts.close': 'Закрити',
    
    // Graph tab
    'graph.title': 'Граф залежностей',
    'graph.filterByType': 'Фільтр за типом',
    'graph.hideDuplicate': 'Приховати дублікати',
    'graph.hideSuperseded': 'Приховати заміщені записи',
    'graph.minConfidence': 'Мін. впевненість',
    'graph.suggestLinks': 'Пропонувати посилання',
    'graph.nodes': 'вузлів',
    'graph.edges': 'ребер',
    
    // Files tab
    'files.name': 'Назва файлу',
    'files.entries': 'Записи',
    'files.search': 'Пошук файлів...',
    'files.noFiles': 'Файлів не знайдено.',
    'files.summary': 'Резюме',
    'files.fileEntries': 'Записи файлу',
    'files.noSummary': 'Резюме недоступне.',
    'files.sortByName': 'Сортувати за назвою',
    'files.sortByCount': 'Сортувати за кількістю',
    
    // Settings tab
    'settings.title': 'Параметри',
    'settings.systemSettings': 'Системні параметри',
    'settings.saveSettings': 'Зберегти параметри',
    'settings.tagEditor': 'Редактор тегів',
    'settings.addTag': 'Додати тег',
    'settings.operations': 'Операції',
    'settings.decay': 'Розпад',
    'settings.deduplicate': 'Дедублікація',
    'settings.renderWiki': 'Відтворити вікі',
    'settings.lint': 'Перевірка',
    'settings.dryRun': 'Попередній перегляд',
    'settings.apply': 'Застосувати',
    'settings.running': 'Виконання...',
    'settings.complete': 'Завершено',
    'settings.error': 'Помилка',
    'settings.newTag': 'Введіть новий тег',
    
    // Settings - Detailed sections
    'settings.resetDefaults': 'Повернути за замовчуванням',
    'settings.confidenceDecay': 'Розпад впевненості',
    'settings.enableDecay': 'Увімкнути розпад',
    'settings.halfLife': 'Період напіврозпаду (днів)',
    'settings.minConfFloor': 'Мінімальна впевненість',
    'settings.semanticDeduplicate': 'Семантична дедублікація',
    'settings.enableDeduplicate': 'Увімкнути дедублікацію',
    'settings.similarityThreshold': 'Поріг подібності',
    'settings.revertDetection': 'Виявлення повернень',
    'settings.enableRevertDetection': 'Увімкнути виявлення повернень',
    'settings.revertPairThreshold': 'Поріг пари повернення',
    'settings.staleEntryCheck': 'Перевірка застарілих записів',
    'settings.minEntryAge': 'Мінімальний вік запису (днів)',
    'settings.wiki': 'Вікі',
    'settings.autoRenderWiki': 'Автоматичне відтворення вікі при add_memory',
    'settings.query': 'Запит',
    'settings.defaultTopK': 'Результати top-k за замовчуванням',
    'settings.decayBlendWeight': 'Вага змішування розпаду (0–1)',
    'settings.defaultTags': 'Теги за замовчуванням',
    'settings.addNewTag': '+ Додати',
    'settings.dryRun': 'Пробний запуск',
    
    // Operations panel
    'op.decayDesc': 'Застосувати розпад до застарілих записів.',
    'op.dedupDesc': 'Знайти і об\'єднати майже однакові записи.',
    'op.wikiDesc': 'Перегенерувати всі сторінки вікі u markdown.',
    'op.lintDesc': 'Запустити перевірки сховища пам\'яті.',
    'op.runDecay': 'Запустити розпад',
    'op.runDedup': 'Запустити дедублікацію',
    'op.renderWiki': 'Відтворити вікі',
    'op.runLint': 'Запустити перевірку',
    'op.lintCheck': 'Перевірка коду',
    
    // Conflict card labels
    'conflicts.cardConflict': 'Конфлікт',
    'conflicts.cardSimilarity': 'Подібність',
    'conflicts.cardResolved': 'вирішено',
    
    // Graph filters and controls
    'graph.filterType': 'Фільтр за типом:',
    'graph.pausePhysics': '⏸ Паузувати фізику',
    'graph.fit': '⊡ Вмістити',
    'graph.reload': '↻ Перезавантажити',
    'graph.hideSuperseded': 'Приховати заміщені',
    'graph.decision': 'Рішення',
    'graph.bugFix': 'Виправлення помилки',
    'graph.feature': 'Функція',
    'graph.note': 'Примітка',
    
    // Entries filters
    'entries.filterType': 'Фільтр за типом:',
    'entries.decision': 'Рішення',
    'entries.bugFix': 'Виправлення помилки',
    'entries.feature': 'Функція',
    'entries.note': 'Примітка',
    'entries.filterStatus': 'Статус:',
    'entries.hideSuperseded': 'Приховати заміщені',
    'entries.minConf': 'Мін. впевненість:',
    
    // Conflict table header
    'conflicts.type': 'Тип',
    'conflicts.status': 'Статус',
    'conflicts.confidence': 'Впевненість',
    'conflicts.description': 'Опис',
    
    // Other UI elements
    'ui.translate': 'Перекласти українською',
    'ui.resizable': 'Розтягується мишкою',
    
    // Notifications
    'toast.success': 'Успішно',
    'toast.error': 'Помилка',
    'toast.info': 'Інформація',
    'toast.warning': 'Попередження',
    
    // Dialog
    'dialog.confirm': 'Підтвердити',
    'dialog.cancel': 'Скасувати',
    'dialog.yes': 'Так',
    'dialog.no': 'Ні',
    
    // Loading & Empty states
    'empty.loading': 'Завантаження...',
    'empty.noData': 'Дані недоступні',
    'empty.noSelection': 'Немає виділення',
    
    // Language
    'lang.en': 'English',
    'lang.uk': 'Українська',
  }
};

// ── Language manager ──────────────────────────────────────────────────────────
let currentLang = localStorage.getItem('uiLang') || 'en';

export function setLanguage(lang) {
  if (translations[lang]) {
    currentLang = lang;
    localStorage.setItem('uiLang', lang);
    return true;
  }
  return false;
}

export function getLanguage() {
  return currentLang;
}

export function t(key, defaultValue = key) {
  return translations[currentLang]?.[key] || translations.en[key] || defaultValue;
}

export function getAllLanguages() {
  return Object.keys(translations);
}
