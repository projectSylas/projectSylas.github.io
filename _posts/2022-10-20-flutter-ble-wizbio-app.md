---
layout: post
title: Flutter PCR 의료기기 진단 앱 개발 - BLE UART 통신과 Ct 값 알고리즘 이식
subtitle: 위즈바이오 Cleo One PCR 진단기 — Nordic UART BLE 프로토콜, CRC 통신, C# 알고리즘 Dart 포팅, Single→Multi 버전
author: HyeongJin
date: 2022-10-20 10:00:00 +0900
categories: React
tags: [Flutter, Dart, BLE, mobile]
sidebar: []
published: true
---

서울애널리티카에서 위즈바이오의 PCR 의료기기 진단 앱을 맡았다. 대상 기기는 Cleo One — COVID-19를 포함한 다중 채널 PCR 진단기다.

앱은 BLE로 기기와 통신해서 PCR 형광 데이터를 실시간으로 수집하고, Ct(Cycle Threshold) 값을 산출해서 양성/음성을 판정한다. 기존 C#으로 작성된 Ct 값 알고리즘을 Dart로 이식하는 게 핵심 과제였다. App Store / Play Store 출시까지 담당했고, 이후 단일 기기(Single)에서 다중 기기 동시 테스트(Multi) 버전으로 확장했다.

## BLE 통신 구조 — Nordic UART

Cleo One은 Nordic Semiconductor의 UART 서비스 프로토콜로 통신한다.

```dart
// Nordic UART Service UUID
const String UART_SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E";
const String UART_TX_UUID      = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"; // 앱→기기
const String UART_RX_UUID      = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"; // 기기→앱
```

BLE 스캔 시 "CLEO" 이름 prefix로 기기만 필터링하고, 연결 후 UART 서비스를 탐색해 TX/RX Characteristic을 분리해서 잡는다.

```dart
Future<void> connectAndPair(String testerId) async {
  await device.connect();
  final services = await device.discoverServices();

  for (var service in services) {
    if (service.uuid.toString().toUpperCase() == UART_SERVICE_UUID) {
      for (var char in service.characteristics) {
        if (char.uuid.toString().toUpperCase() == UART_RX_UUID) {
          // 수신 알림 구독
          await char.setNotifyValue(true);
          char.lastValueStream.listen((data) {
            _handleMessage(String.fromCharCodes(data));
          });
        }
        if (char.uuid.toString().toUpperCase() == UART_TX_UUID) {
          txCharacteristic = char;
        }
      }
    }
  }
}
```

## CRC16 통신 프로토콜

기기와의 메시지 신뢰성을 위해 CRC16을 사용했다. 메시지를 보낼 때마다 CRC를 계산해서 뒤에 붙이고, 수신 시 CRC를 검증해서 손상 여부를 판단한다.

```dart
String attachCrc(String msg) {
  int crc = CrcLib.calculateCrc16(msg.codeUnits);
  String crcStr = crc.toRadixString(16).padLeft(4, '0').toUpperCase();
  return '$msg$crcStr\r\n';
}

bool verifyCrc(String rawMsg) {
  // 마지막 6자 = CRC(4) + CRLF(2)
  String payload = rawMsg.substring(0, rawMsg.length - 6);
  String receivedCrc = rawMsg.substring(rawMsg.length - 6, rawMsg.length - 2);
  int calculated = CrcLib.calculateCrc16(payload.codeUnits);
  return calculated.toRadixString(16).toUpperCase().padLeft(4, '0') == receivedCrc;
}
```

BLE MTU 제한 때문에 한 번에 보낼 수 있는 데이터 크기가 제한된다. 긴 메시지는 20바이트 청크로 나눠서 순차 전송했다.

```dart
Future<void> sendMsg(String msg) async {
  final bytes = Uint8List.fromList(msg.codeUnits);
  // 20바이트씩 청크 분할
  final chunks = chunkArray(bytes, 20);
  for (var chunk in chunks) {
    await txCharacteristic!.write(chunk, withoutResponse: true);
  }
}

List<Uint8List> chunkArray(Uint8List data, int size) {
  List<Uint8List> chunks = [];
  for (int i = 0; i < data.length; i += size) {
    int end = (i + size < data.length) ? i + size : data.length;
    chunks.add(data.sublist(i, end));
  }
  return chunks;
}
```

## PCR 데이터 수집 — SP/SC 사이클

PCR 측정은 두 단계로 나뉜다.

- **SP(Stabilization Phase)**: 30사이클 — 기준선 안정화
- **SC(Signal Collection)**: 50사이클 — 실제 증폭 데이터 수집

```dart
// SP 30사이클 스트림 수집
Future<List<double>> streamCollectSP() async {
  List<double> spData = [];
  await for (var value in spStream) {
    spData.add(value);
    if (spData.length >= 30) break;
  }
  return spData;
}

// SC 50사이클 스트림 수집
Future<List<double>> streamCollectSC() async {
  List<double> scData = [];
  await for (var value in scStream) {
    scData.add(value);
    if (scData.length >= 50) break;
  }
  return scData;
}
```

## C# → Dart: PCR Ct 값 알고리즘 이식

가장 어려운 부분이었다. Ct(Cycle Threshold) 값은 PCR 형광 신호가 기준값을 넘는 사이클 번호다. COVID-19 판정에서 Ct 값이 특정 임계값 이하이면 양성이다.

C#으로 작성된 `FittingDataCalculator` 클래스를 Dart로 그대로 이식했다.

```dart
class FittingDataCalculator {
  static const int MAX_CYCLE = 50;
  static const int BASELINE_START_POINT = 5;

  final double ctValue;  // 기본값 50 (COVID-19 기준)
  CalcMode calcMode;

  FittingDataCalculator({
    this.ctValue = 50.0,
    this.calcMode = CalcMode.SUBSTRACT_CURVE_FIT,
  });

  // 3채널 PCR 데이터 처리
  Map<String, dynamic> pcrDataProcess(
    List<double> ch1Raw,
    List<double> ch2Raw,
    List<double> ch3Raw,
  ) {
    // 1. 이동 평균 필터 (노이즈 제거)
    final ch1Filtered = movingAverage(ch1Raw);
    final ch2Filtered = movingAverage(ch2Raw);
    final ch3Filtered = movingAverage(ch3Raw);

    // 2. 사이클 간 변화량으로 변환
    final ch1Delta = toDelta(ch1Filtered);
    final ch2Delta = toDelta(ch2Filtered);
    final ch3Delta = toDelta(ch3Filtered);

    // 3. 베이스라인 보정 (BASELINE_START_POINT 이전 평균 제거)
    final ch1Corrected = baselineCorrect(ch1Delta);
    final ch2Corrected = baselineCorrect(ch2Delta);
    final ch3Corrected = baselineCorrect(ch3Delta);

    // 4. Ct 값 산출
    double ct1 = calcCt(ch1Corrected);
    double ct2 = calcCt(ch2Corrected);
    double ct3 = calcCt(ch3Corrected);

    return {'ct1': ct1, 'ct2': ct2, 'ct3': ct3};
  }

  double calcCt(List<double> data) {
    for (int i = BASELINE_START_POINT; i < data.length; i++) {
      if (data[i] >= ctValue) return i.toDouble();
    }
    return MAX_CYCLE.toDouble();  // 미검출
  }
}
```

**타입 차이 대응**

C#의 `float[]`는 Dart에서 `List<double>`로 변환했다. C#의 float는 32비트인데, Dart의 double은 64비트다. BLE로 4바이트 float를 받을 때 정밀도 차이가 생겨서 `ByteData.getFloat32()`로 맞췄다.

```dart
// BLE에서 받은 4바이트를 C# float와 동일하게 파싱
double parseFloat32(Uint8List bytes, int offset) {
  final bd = ByteData.sublistView(bytes);
  return bd.getFloat32(offset, Endian.little);
}
```

## Provider 패턴으로 BLE 상태 관리

BLE 연결 상태는 `Provider` 패턴으로 관리했다. 여러 화면에서 기기 상태를 공유해야 하기 때문이다.

```dart
class BluetoothProvider extends ChangeNotifier {
  CleoDevice? currentDevice;
  List<BluetoothDevice> scanResults = [];

  // CLEO 기기만 스캔 필터링
  Future<void> scan() async {
    await FlutterBluePlus.startScan(
      withNames: ["CLEO"],
      timeout: const Duration(seconds: 10),
    );
    FlutterBluePlus.scanResults.listen((results) {
      scanResults = results.map((r) => r.device).toList();
      notifyListeners();
    });
  }

  Future<void> connect(BluetoothDevice device, String testerId) async {
    currentDevice = CleoDevice(device);
    await currentDevice!.connectAndPair(testerId);
    notifyListeners();
  }
}
```

## Single → Multi 버전 확장

단일 기기 앱이 안정화된 후, 여러 기기를 동시에 연결해서 테스트하는 Multi 버전을 개발했다.

Single 버전은 `CleoDevice?` 하나를 관리했지만, Multi 버전은 `Map`으로 여러 기기를 동시 관리한다.

```dart
// Single: 단일 기기
class BluetoothProvider extends ChangeNotifier {
  CleoDevice? currentDevice;
}

// Multi: 다중 기기 동시 관리
class BluetoothMultiProvider extends ChangeNotifier {
  // DeviceIdentifier → CleoDevice 매핑
  Map<DeviceIdentifier, CleoDevice> currentDeviceMap = {};
  // 슬롯 인덱스 → CleoDevice 매핑
  Map<int, CleoDevice?> currentDeviceList = {};
  int index = 0;  // 현재 선택된 슬롯

  Future<void> connect(BluetoothDevice device, String testerId) async {
    final cleoDevice = CleoDevice(device);
    cleoDevice.setIndex(index);
    await cleoDevice.connectAndPair(testerId);

    currentDeviceMap[device.remoteId] = cleoDevice;
    currentDeviceList[index] = cleoDevice;
    notifyListeners();
  }

  // 특정 기기만 선택적 연결 해제
  Future<void> selDeviceDisconnect(int slotIndex) async {
    final device = currentDeviceList[slotIndex];
    await device?.disconnect();
    currentDeviceList[slotIndex] = null;
    notifyListeners();
  }
}
```

재연결 시에는 이전 테스트 진행 상태(`TestProgressState`)가 있으면 타이머를 재시작해서 중단 없이 이어갔다.

## QR 스캔 카트리지 인식

카트리지 삽입 시 QR 코드를 스캔해서 카트리지 ID와 시약 정보를 자동으로 인식했다.

```dart
import 'package:qr_code_scanner/qr_code_scanner.dart';

void onQRViewCreated(QRViewController controller) {
  controller.scannedDataStream.listen((scanData) {
    final cartridgeId = scanData.code;
    // 카트리지 ID로 시약 정보 조회
    loadCartridgeInfo(cartridgeId);
  });
}
```

## 카트리지 삽입 12단계 UI

PCR 검사는 카트리지 삽입부터 결과까지 12단계 프로세스로 진행된다. 각 단계별로 별도 화면이 있고, 기기에서 오는 상태 메시지에 따라 화면이 전환된다.

```
01. 카트리지 없음 대기
02. 카트리지 삽입 감지
03. 샘플 로딩 중
04. SP(안정화) 시작
05~34. SP 사이클 1~30
35. SC(신호 수집) 시작
36~85. SC 사이클 1~50
86. 결과 계산 중
87. 결과 표시 (양성/음성/재검)
```

## SQLite 로컬 저장 / Firebase Auth

측정 결과는 `sqflite`로 기기 내 SQLite에 저장하고, Firebase Authentication으로 사용자 인증 후 서버에도 동기화했다.

```dart
import 'package:sqflite/sqflite.dart';

Future<void> saveResult(TestResult result) async {
  final db = await openDatabase('cleo_results.db');
  await db.insert('results', result.toMap());
}
```

생체 인증(`local_auth`)도 추가해서 앱 잠금 해제를 지문/얼굴 인식으로 처리했다.

## 배운 것

BLE 통신에서 MTU 제한, 청크 분할, CRC 검증이 세트로 따라온다는 걸 직접 겪었다. 특히 PCR처럼 사이클 단위로 데이터가 연속해서 오는 경우, 스트림 처리를 정확히 설계하지 않으면 데이터 누락이 생긴다.

C# 알고리즘을 Dart로 이식할 때 가장 주의할 점은 수치 타입이다. `float`와 `double`의 정밀도 차이가 Ct 값 계산에 영향을 줄 수 있어서, `ByteData.getFloat32()`로 C#과 동일한 32비트 파싱을 맞추는 게 중요했다.

Single → Multi 확장은 Provider 패턴의 `Map` 구조 변경이 핵심이었다. 기기별로 독립된 상태를 유지하면서도 슬롯 인덱스로 UI와 연결하는 방식이 효과적이었다.
