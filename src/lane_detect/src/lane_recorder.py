# python3 inference_unet.py --netType Unet --GPUs 0 --batchSize 1

import sys
#print(sys.path)
#sys.path.append('/lib/python3.7/site-packages')
import opts
import math
import importlib
import os

import rospy
import _init_paths
import torch
import torchvision.transforms as transforms
from torch.autograd import Variable
import torch.nn.functional as F

import matplotlib.pyplot as plt

import cv2
import numpy as np
from PIL import Image
from warper import Warper
from slidewindow import SlideWindow

import datetime
import time
from pidcal import PidCal
from race.msg import drive_values

ack_publisher = None
car_run_speed = 0
max_speed = 5
prevX = 0

#drive_values_publisher = rospy.Publisher('control_value', drive_values, queue_size=1)

rospy.init_node("lane_detect")

drive_values_pub = rospy.Publisher('control_value', drive_values, queue_size=1)

def lpf(x, alpha):
    global prevX

    x_lpf = alpha*prevX + (1-alpha)*x
    prevX = x_lpf

    return x_lpf

def auto_drive(pid):
    global car_run_speed
    global max_speed

    if car_run_speed <= max_speed:
        car_run_speed += 0.1

    drive_value = drive_values()

    drive_value.throttle = int(car_run_speed)
    #drive_value.throttle = 4
    drive_value.steering = pid*1.5
    #drive_value.steering = pid

    drive_values_pub.publish(drive_value)

    print("steer : ", drive_value.steering)
    print("throttle : ", drive_value.throttle)

def normalize(ipt, mean, std):
    ipt[:][:][0] = (ipt[:][:][0] - mean[0]) / std[0]
    ipt[:][:][1] = (ipt[:][:][1] - mean[1]) / std[1]
    ipt[:][:][2] = (ipt[:][:][2] - mean[2]) / std[2]
    return ipt

def unNormalize(ipt, mean, std):
    ipt[:][:][0] = (ipt[:][:][0] * std[0]) + mean[0]
    ipt[:][:][1] = (ipt[:][:][1] * std[1]) + mean[1]
    ipt[:][:][2] = (ipt[:][:][2] * std[2]) + mean[2]
    return ipt

def preprocess_img(im):
    mean = np.array([0.485, 0.456, 0.406])
    std = np.array([0.229, 0.224, 0.225])
    im = np.asarray(im)
    im = normalize(im, mean, std)
    im = np.transpose(im, (2, 0, 1))
    return im

def postprocess_img(im):
    mean = np.array([0.485, 0.456, 0.406])
    std = np.array([0.229, 0.224, 0.225])
    im = np.transpose(im, (1, 2, 0))
    im = unNormalize(im, mean, std)
    return im

def main():
    os.environ["CUDA_VISIBLE_DEVICES"] = '0'
    #cudnn.benchmark = True
    pidcal = PidCal()
    opt = opts.parse()
    warper = Warper()
    slidewindow = SlideWindow()

    print(("device id: {}".format(torch.cuda.current_device())))
    print("torch.version",torch.__version__)
    print("cuda_version",torch.version.cuda)


    models = importlib.import_module('models.init')
    # print(models)
    criterions = importlib.import_module('criterions.init')
    checkpoints = importlib.import_module('checkpoints')
    Trainer = importlib.import_module('models.' + opt.netType + '-train')

    # Data loading
    print('=> Setting up data loader')

    # Load previous checkpoint, if it exists
    print('=> Checking checkpoints')
    checkpoint = checkpoints.load(opt)

    # Create model
    model, optimState = models.setup(opt, checkpoint)
    model.cuda()

    criterion = criterions.setup(opt, checkpoint, model)

    ##################################################################################

    model.eval()

    cap = None

    if opt.video_idx is 0:
        cap = cv2.VideoCapture("input_video/720p.mp4")
    elif opt.video_idx is 1:
        cap = cv2.VideoCapture("input_video/straight.avi")
    elif opt.video_idx is 2:
        cap = cv2.VideoCapture("input_video/test.avi")
    elif opt.video_idx is 3:
        cap = cv2.VideoCapture("input_video/track.avi")
    elif opt.video_idx is 4:
        cap =cv2.VideoCapture("output_video/field.avi")
    else:
        cap = cv2.VideoCapture(0)
        # video test
        cap.set(3,1280)
        cap.set(4,720)

    if cap.isOpened():
        print("width : {}, height : {}".format(cap.get(3), cap.get(4)))

    video_width = int(cap.get(3))
    video_height = int(cap.get(4))



    #fourcc = cv2.VideoWriter_fourcc('M','J','P','G')
    # out = cv2.VideoWriter('output_video/TEST_1.avi', fourcc, 20.0, (1280,480),0)

    prev_time = 0

    fps_list = []

    now = datetime.datetime.now()

    fourcc = None
    out = None

    if opt.video_idx > 2:
        # fourcc =cv2.VideoWriter_fourcc(*'MJPG')
        fourcc =cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter('output_warper_video/' + str(now) + '.avi',fourcc,30.0,(480,320))

    pid_list=list()
    steer_list = list()
    lpf_list = list()

    pid_old = None
    steer_theta = 0
    i=0
    x_location = 440

    while True:
        ret, frame = cap.read()
        if frame is None:
            break
        frame_height, frame_width, frame_channels = frame.shape

        print("Frame Info : (Height, Width, Channels) : ({}, {}, {})".format(frame_height, frame_width, frame_channels))

        # record_frame = cv2.resize(frame, (1280,720))
        # record_frame = cv2.resize(frame, (1280,720))

        if ret:
            cur_time = time.time()
            frame_new = cv2.resize(frame, (320,180))

            input_img = frame_new / 255.
            input_img = preprocess_img(input_img)

            # array to tensor
            input_img = torch.from_numpy(input_img).float()

            with torch.no_grad():
                inputData_var = Variable(input_img).unsqueeze(0).cuda()

                # inference
                output = model.forward(inputData_var)
                output = torch.sigmoid(output)
                #output = F.softmax(output, dim=1)

                # gpu -> cpu,  tensor -> numpy
                output = output.detach().cpu().numpy()

                output = output[0]

                output = postprocess_img(output)
                output = np.clip(output, 0, 1)
                output *= 255
                output = np.uint8(output)


                output = cv2.resize(output, (640, 360))
                output[output>80] = 255
                output[output<=80] = 0
                cv2.circle(output, (output.shape[1]/2, output.shape[0]), 9, (255,255,0), -1)
                cv2.imshow("output_img",output)
                print("shape_info",output.shape)
                # cv2.circle(output, (output.shape[0]/2, output.shape[1]/2), 9, (0,255,0), -1)
                #warper_img = warper.warp(output)
                warper_img = warper.warp_test(output)
                cv2.imshow("warp_img",warper_img)
                if opt.video_idx > 2 :
                        # print("frame.shape : {}".format(frame.shape))
                    print("warper_img.shape : {}".format(warper_img.shape))

                    out.write(warper_img)
                # warper_img_test = warper.warp_test(output)
                # cv2.imshow("warp_img_test",warper_img_test)
                ret, left_start_x, right_start_x, cf_img = slidewindow.w_slidewindow(warper_img, 180)

                if ret:
                    i+=1
                    left_x_current,right_x_current, sliding_img,steer_theta = slidewindow.h_slidewindow(warper_img, left_start_x, right_start_x)
                    cv2.imshow('sliding_img', sliding_img)
                    steer_list.append(steer_theta)

                    x_location = (left_x_current+right_x_current)/2

                    # low pass filter
                    lpf_result = lpf(x_location, 0.3)
                    lpf_list.append(lpf_result)

                    # steer theta : Degree
                    print("steer theta:" ,steer_theta)

                    if steer_theta<-28.0 or steer_theta >28.0:
                        # auto_drive(pid_old)
                        auto_drive(steer_theta)

                    else:
                        # degree angle
                        pid = round(pidcal.pid_control(lpf_result),6)
                        pid_list.append(pid)
                        print("pid :",pid)
                        pid_old = pid
                        auto_drive(steer_theta)

                        # auto_drive(pid)
                else:
                    auto_drive(steer_theta)

                    # auto_drive(pid)
                    pidcal.error_sum = 0
                    pidcal.error_old = 0


                end_time = time.time()
                sec = end_time - cur_time

                fps = 1/sec
                fps_list.append(fps)

                print("Estimated fps {0} " . format(fps))

                # out.write(add_img)

                cv2.imshow("frame",frame)



                # cv2.imshow("src", warper_img)
                # cv2.imshow("out_img", output)
                cv2.imshow("cf_img", cf_img)

                key = cv2.waitKey(1) & 0xFF
                if key == 27: break
                elif key == ord('p'):
                    cv2.waitKey(-1)

    cap.release()
    cv2.destroyAllWindows()


    # plt.show()


if __name__ == '__main__':
    main()
