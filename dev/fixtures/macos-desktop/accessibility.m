// Disposable AppKit provider. State records application effects, not AX receipts.
#import <AppKit/AppKit.h>

@interface Fixture : NSObject <NSApplicationDelegate, NSTableViewDataSource>
@property(nonatomic, strong) NSWindow *window;
@property(nonatomic, strong) NSTextView *editor;
@property(nonatomic, strong) NSTextView *document;
@property(nonatomic, strong) NSButton *toggle;
@property(nonatomic, strong) NSSlider *numeric;
@property(nonatomic, strong) NSTableView *table;
@property(nonatomic, strong) NSButton *dynamic;
@property(nonatomic, copy) NSString *statePath;
@property(nonatomic, strong) NSTimer *timer;
@property(nonatomic, assign) NSUInteger clicks;
@end

@implementation Fixture
- (void)apply:(id)sender {
    self.clicks++;
    if (self.dynamic) {
        [self.dynamic removeFromSuperview];
        self.dynamic = nil;
    } else {
        self.dynamic = [NSButton buttonWithTitle:@"Dynamic control" target:nil action:nil];
        self.dynamic.frame = NSMakeRect(560, 350, 220, 30);
        self.dynamic.accessibilityIdentifier = @"fixture-dynamic";
        [self.window.contentView addSubview:self.dynamic];
    }
}
- (NSInteger)numberOfRowsInTableView:(NSTableView *)tableView { return 2; }
- (id)tableView:(NSTableView *)tableView objectValueForTableColumn:(NSTableColumn *)column row:(NSInteger)row {
    return [NSString stringWithFormat:@"%@ %ld", column.identifier, (long)row];
}
- (void)publish:(NSTimer *)timer {
    NSRange selection = self.document.selectedRange;
    NSMutableArray *selectedRows = [NSMutableArray array];
    [self.table.selectedRowIndexes enumerateIndexesUsingBlock:^(NSUInteger index, BOOL *stop) {
        [selectedRows addObject:@(index)];
    }];
    NSDictionary *state = @{
        @"ready": @(self.window.visible), @"pid": @(NSProcessInfo.processInfo.processIdentifier),
        @"clicks": @(self.clicks), @"text": self.editor.string,
        @"checked": @(self.toggle.state == NSControlStateValueOn),
        @"numeric": @(self.numeric.doubleValue),
        @"document_text": self.document.string,
        @"document_selection": [self.document.string substringWithRange:selection],
        @"editor_focused": @(self.window.firstResponder == self.editor),
        @"window_main": @(self.window.mainWindow),
        @"window_minimized": @(self.window.miniaturized),
        @"window_frame": NSStringFromRect(self.window.frame),
        @"window_x": @(self.window.frame.origin.x),
        @"window_y": @(NSMaxY(NSScreen.screens.firstObject.frame) - NSMaxY(self.window.frame)),
        @"window_width": @(self.window.frame.size.width),
        @"window_height": @(self.window.frame.size.height),
        @"window_scale": @(self.window.backingScaleFactor),
        @"selected_row": @(self.table.selectedRow),
        @"selected_rows": selectedRows,
        @"dynamic": @(self.dynamic != nil)
    };
    NSError *error = nil;
    NSData *data = [NSJSONSerialization dataWithJSONObject:state options:0 error:&error];
    if (!data || ![data writeToFile:self.statePath options:NSDataWritingAtomic error:&error]) {
        NSLog(@"Cannot publish fixture state: %@", error);
        [NSApp terminate:nil];
    }
}
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    self.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(100, 100, 900, 700)
        styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable | NSWindowStyleMaskMiniaturizable
        backing:NSBackingStoreBuffered defer:NO];
    self.window.title = @"Envd accessibility fixture";
    self.window.releasedWhenClosed = NO;
    NSView *content = self.window.contentView;
    NSButton *apply = [NSButton buttonWithTitle:@"Apply changes" target:self action:@selector(apply:)];
    apply.frame = NSMakeRect(24, 630, 220, 36);
    apply.accessibilityIdentifier = @"fixture-apply";
    [content addSubview:apply];
    NSMutableString *longName = [NSMutableString string];
    for (NSUInteger i = 0; i < 40; i++) [longName appendString:@"Segment "];
    [longName appendString:@"Straße 世界😀 END"];
    NSButton *longLabel = [NSButton buttonWithTitle:longName target:nil action:nil];
    longLabel.frame = NSMakeRect(270, 630, 560, 36);
    longLabel.accessibilityIdentifier = @"fixture-long-name";
    [content addSubview:longLabel];

    NSScrollView *editorScroll = [[NSScrollView alloc] initWithFrame:NSMakeRect(24, 500, 820, 112)];
    editorScroll.hasVerticalScroller = YES;
    editorScroll.hasHorizontalScroller = YES;
    editorScroll.autohidesScrollers = NO;
    editorScroll.scrollerStyle = NSScrollerStyleLegacy;
    editorScroll.accessibilityIdentifier = @"fixture-scroll";
    self.editor = [[NSTextView alloc] initWithFrame:editorScroll.bounds];
    self.editor.string = @"Initial text";
    self.editor.richText = NO;
    self.editor.accessibilityLabel = @"Unicode editor";
    self.editor.accessibilityIdentifier = @"fixture-editor";
    editorScroll.documentView = self.editor;
    [content addSubview:editorScroll];

    NSTextField *readOnly = [[NSTextField alloc] initWithFrame:NSMakeRect(24, 450, 500, 28)];
    readOnly.stringValue = @"Read-only text";
    readOnly.editable = NO;
    readOnly.accessibilityIdentifier = @"fixture-readonly";
    [content addSubview:readOnly];
    NSSecureTextField *secure = [[NSSecureTextField alloc] initWithFrame:NSMakeRect(24, 400, 500, 28)];
    secure.stringValue = @"fixture-secret";
    secure.accessibilityIdentifier = @"fixture-protected";
    [content addSubview:secure];

    self.toggle = [NSButton checkboxWithTitle:@"Enable feature" target:nil action:nil];
    self.toggle.frame = NSMakeRect(24, 350, 300, 30);
    self.toggle.accessibilityIdentifier = @"fixture-toggle";
    [content addSubview:self.toggle];
    self.numeric = [NSSlider sliderWithValue:20 minValue:0 maxValue:100 target:nil action:nil];
    self.numeric.frame = NSMakeRect(24, 300, 500, 30);
    self.numeric.accessibilityIdentifier = @"fixture-numeric";
    [content addSubview:self.numeric];

    NSScrollView *documentScroll = [[NSScrollView alloc] initWithFrame:NSMakeRect(24, 120, 820, 150)];
    documentScroll.hasVerticalScroller = YES;
    self.document = [[NSTextView alloc] initWithFrame:documentScroll.bounds];
    self.document.string = @"Hello 世界😀\nSecond line";
    self.document.accessibilityLabel = @"Native document";
    self.document.accessibilityIdentifier = @"fixture-document";
    documentScroll.documentView = self.document;
    [content addSubview:documentScroll];
    NSButton *disabled = [NSButton buttonWithTitle:@"Disabled action" target:self action:@selector(apply:)];
    disabled.frame = NSMakeRect(24, 55, 220, 36);
    disabled.enabled = NO;
    disabled.accessibilityIdentifier = @"fixture-disabled";
    [content addSubview:disabled];

    NSScrollView *tableScroll = [[NSScrollView alloc] initWithFrame:NSMakeRect(24, 8, 820, 40)];
    NSTableView *table = [[NSTableView alloc] initWithFrame:tableScroll.bounds];
    self.table = table;
    table.accessibilityIdentifier = @"fixture-table";
    table.allowsMultipleSelection = YES;
    table.dataSource = self;
    for (NSString *identifier in @[@"First", @"Second"]) {
        NSTableColumn *column = [[NSTableColumn alloc] initWithIdentifier:identifier];
        column.title = identifier;
        column.width = 300;
        [table addTableColumn:column];
    }
    tableScroll.documentView = table;
    [content addSubview:tableScroll];

    [self.window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
    self.timer = [NSTimer scheduledTimerWithTimeInterval:0.05 target:self selector:@selector(publish:)
        userInfo:nil repeats:YES];
    [self publish:nil];
}
- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender { return YES; }
@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 2) return 2;
        NSApplication *app = NSApplication.sharedApplication;
        [app setActivationPolicy:NSApplicationActivationPolicyRegular];
        Fixture *fixture = [Fixture new];
        fixture.statePath = [NSString stringWithUTF8String:argv[1]];
        app.delegate = fixture;
        [app run];
        [fixture.timer invalidate];
    }
    return 0;
}
